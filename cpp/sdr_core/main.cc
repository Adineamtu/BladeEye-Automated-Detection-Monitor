#include "ring_buffer.h"
#include "shm_layout.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <complex>
#include <csignal>
#include <fcntl.h>
#include <cstring>
#include <fftw3.h>
#include <iostream>
#include <mutex>
#include <netinet/in.h>
#include <optional>
#include <sstream>
#include <string>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <ctime>

namespace {

using Complex = std::complex<float>;
constexpr const char* kShmName = "/sdr_core_spectrum";
constexpr const char* kAlertSocket = "/tmp/sdr_core_alert.sock";
constexpr const char* kCmdSocket = "/tmp/sdr_core_cmd.sock";
constexpr std::size_t kFftSize = sdr::kSpectrumBins;
constexpr std::size_t kRingCapacity = 128;
constexpr std::size_t kAverageFrames = 8;

struct RuntimeConfig {
    std::atomic<uint32_t> sample_rate{20'000'000};
    std::atomic<uint32_t> analog_bandwidth{16'000'000};
    std::atomic<uint64_t> center_freq{433'920'000};
    std::atomic<float> threshold_db{-55.0f};
    std::atomic<float> gain_db{40.0f};
    std::atomic<bool> stream_enabled{true};
    std::atomic<uint32_t> dropped_samples{0};
};

constexpr std::array<uint32_t, 5> kAllowedSampleRates = {
    1'000'000,
    2'000'000,
    5'000'000,
    10'000'000,
    20'000'000,
};

bool is_allowed_rate(uint32_t value) {
    return std::find(kAllowedSampleRates.begin(), kAllowedSampleRates.end(), value) !=
           kAllowedSampleRates.end();
}

void apply_sample_rate_reconfiguration(RuntimeConfig& cfg, uint32_t requested_rate) {
    if (!is_allowed_rate(requested_rate)) {
        std::cerr << "Ignoring unsupported sample-rate request: " << requested_rate << " Hz\n";
        return;
    }

    const auto previous = cfg.sample_rate.load();
    cfg.stream_enabled.store(false);                  // stop stream
    cfg.sample_rate.store(requested_rate);            // set sample rate
    cfg.analog_bandwidth.store(requested_rate * 4U / 5U);  // set analog BW to 0.8x

    const auto delta = (requested_rate > previous) ? (requested_rate - previous) : (previous - requested_rate);
    if (delta >= 10'000'000) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    cfg.stream_enabled.store(true);  // restart stream
}

struct SampleChunk {
    std::array<Complex, kFftSize> iq{};
};

std::atomic<bool> g_running{true};

void signal_handler(int) { g_running.store(false); }

class SharedMemoryWriter {
public:
    SharedMemoryWriter() {
        fd_ = shm_open(kShmName, O_CREAT | O_RDWR, 0660);
        if (fd_ < 0) {
            throw std::runtime_error("shm_open failed");
        }
        if (ftruncate(fd_, sizeof(sdr::SharedSpectrumFrame)) != 0) {
            throw std::runtime_error("ftruncate shared memory failed");
        }
        void* ptr = mmap(nullptr,
                         sizeof(sdr::SharedSpectrumFrame),
                         PROT_READ | PROT_WRITE,
                         MAP_SHARED,
                         fd_,
                         0);
        if (ptr == MAP_FAILED) {
            throw std::runtime_error("mmap failed");
        }
        frame_ = reinterpret_cast<sdr::SharedSpectrumFrame*>(ptr);
        std::memset(frame_, 0, sizeof(sdr::SharedSpectrumFrame));
    }

    ~SharedMemoryWriter() {
        if (frame_) {
            munmap(frame_, sizeof(sdr::SharedSpectrumFrame));
        }
        if (fd_ >= 0) {
            close(fd_);
        }
    }

    void publish(uint64_t frame_id,
                 uint32_t sample_rate,
                 uint32_t analog_bandwidth,
                 uint64_t center_freq,
                 uint64_t last_heartbeat,
                 uint32_t dropped_samples,
                 float buffer_fill_percent,
                 float processing_latency_ms,
                 float cpu_usage,
                 const std::array<float, sdr::kSpectrumBins>& bins,
                 const std::vector<sdr::PeakEvent>& peaks) {
        frame_->header.frame_id = frame_id;
        frame_->header.sample_rate = sample_rate;
        frame_->header.analog_bandwidth = analog_bandwidth;
        frame_->header.center_freq = center_freq;
        frame_->header.last_heartbeat = last_heartbeat;
        frame_->header.dropped_samples = dropped_samples;
        frame_->header.buffer_fill_percent = buffer_fill_percent;
        frame_->header.processing_latency_ms = processing_latency_ms;
        frame_->header.cpu_usage = cpu_usage;
        frame_->spectrum_data = bins;
        const auto count = std::min(peaks.size(), frame_->peaks.size());
        frame_->header.peak_count = static_cast<uint32_t>(count);
        std::copy_n(peaks.begin(), count, frame_->peaks.begin());
        frame_->header.state.store(1, std::memory_order_release);
    }

private:
    int fd_{-1};
    sdr::SharedSpectrumFrame* frame_{nullptr};
};

class UnixDatagramSocket {
public:
    explicit UnixDatagramSocket(const std::string& path, bool bind_socket) : path_(path) {
        fd_ = socket(AF_UNIX, SOCK_DGRAM, 0);
        if (fd_ < 0) {
            throw std::runtime_error("socket create failed");
        }
        if (bind_socket) {
            sockaddr_un addr{};
            addr.sun_family = AF_UNIX;
            std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path_.c_str());
            unlink(path_.c_str());
            if (bind(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
                throw std::runtime_error("socket bind failed");
            }
        }
    }

    ~UnixDatagramSocket() {
        if (fd_ >= 0) {
            close(fd_);
        }
    }

    void send_to(const std::string& to, const std::string& payload) {
        sockaddr_un addr{};
        addr.sun_family = AF_UNIX;
        std::snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", to.c_str());
        sendto(fd_, payload.data(), payload.size(), 0, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
    }

    std::optional<std::string> recv_one() {
        char buf[256] = {0};
        const auto n = recv(fd_, buf, sizeof(buf) - 1, MSG_DONTWAIT);
        if (n <= 0) {
            return std::nullopt;
        }
        return std::string(buf, static_cast<std::size_t>(n));
    }

private:
    int fd_{-1};
    std::string path_;
};

class SyntheticSource {
public:
    bool read_chunk(SampleChunk& chunk, uint32_t sample_rate, uint64_t center_freq) {
        (void)center_freq;
        constexpr float kPi = 3.1415926535f;
        const float tone = 0.12f * static_cast<float>(sample_rate);
        for (std::size_t i = 0; i < chunk.iq.size(); ++i) {
            const float t = static_cast<float>(cursor_++) / static_cast<float>(sample_rate);
            const float phase = 2.0f * kPi * tone * t;
            chunk.iq[i] = Complex(std::cos(phase), std::sin(phase));
        }
        return true;
    }

private:
    uint64_t cursor_{0};
};

std::vector<float> hann_window() {
    std::vector<float> window(kFftSize);
    constexpr float kPi = 3.1415926535f;
    for (std::size_t i = 0; i < kFftSize; ++i) {
        window[i] = 0.5f * (1.0f - std::cos((2.0f * kPi * static_cast<float>(i)) / (kFftSize - 1)));
    }
    return window;
}

void acquisition_thread(sdr::SpscRingBuffer<SampleChunk, kRingCapacity>& ring, RuntimeConfig& cfg) {
    SyntheticSource source;
    while (g_running.load()) {
        if (!cfg.stream_enabled.load()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        SampleChunk chunk;
        if (!source.read_chunk(chunk, cfg.sample_rate.load(), cfg.center_freq.load())) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }
        while (!ring.push(chunk) && g_running.load()) {
            cfg.dropped_samples.fetch_add(1, std::memory_order_relaxed);
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
    }
}

void command_listener(RuntimeConfig& cfg) {
    UnixDatagramSocket cmd_socket(kCmdSocket, true);
    while (g_running.load()) {
        if (auto msg = cmd_socket.recv_one()) {
            std::istringstream is(*msg);
            std::string command;
            is >> command;
            if (command == "SET_GAIN") {
                float value = 0;
                is >> value;
                cfg.gain_db.store(value);
            } else if (command == "SET_FREQ") {
                uint64_t value = 0;
                is >> value;
                cfg.center_freq.store(value);
            } else if (command == "SET_RATE") {
                uint32_t value = 0;
                is >> value;
                apply_sample_rate_reconfiguration(cfg, value);
            } else if (command.rfind("SET_BW:", 0) == 0) {
                const auto value = static_cast<uint32_t>(std::stoul(command.substr(7)));
                apply_sample_rate_reconfiguration(cfg, value);
            } else if (command == "SET_THRESHOLD") {
                float value = -60;
                is >> value;
                cfg.threshold_db.store(value);
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
}

void processing_thread(sdr::SpscRingBuffer<SampleChunk, kRingCapacity>& ring, RuntimeConfig& cfg) {
    SharedMemoryWriter shm;
    UnixDatagramSocket alert_tx("/tmp/sdr_core_alert_tx.sock", true);

    std::vector<Complex> fft_in(kFftSize);
    std::vector<fftwf_complex> fft_out(kFftSize);
    auto* plan = fftwf_plan_dft_1d(static_cast<int>(kFftSize),
                                   reinterpret_cast<fftwf_complex*>(fft_in.data()),
                                   fft_out.data(),
                                   FFTW_FORWARD,
                                   FFTW_ESTIMATE);

    auto window = hann_window();
    std::array<float, sdr::kSpectrumBins> smoothed{};
    uint64_t frame_id = 0;
    auto previous_loop = std::chrono::steady_clock::now();

    while (g_running.load()) {
        auto chunk = ring.pop();
        if (!chunk) {
            std::this_thread::sleep_for(std::chrono::microseconds(200));
            continue;
        }
        const auto loop_start = std::chrono::steady_clock::now();

        for (std::size_t i = 0; i < kFftSize; ++i) {
            fft_in[i] = chunk->iq[i] * window[i];
        }

        fftwf_execute(plan);

        std::array<float, sdr::kSpectrumBins> spectrum{};
        std::vector<sdr::PeakEvent> peaks;
        const float threshold = cfg.threshold_db.load();
        const auto sample_rate = cfg.sample_rate.load();
        const auto center_freq = cfg.center_freq.load();
        for (std::size_t i = 0; i < kFftSize; ++i) {
            const float re = fft_out[i][0];
            const float im = fft_out[i][1];
            const float p = (re * re + im * im) / static_cast<float>(kFftSize);
            const float db = 10.0f * std::log10(std::max(p, 1e-12f));
            smoothed[i] = ((kAverageFrames - 1) * smoothed[i] + db) / static_cast<float>(kAverageFrames);
            spectrum[i] = smoothed[i];
            if (spectrum[i] > threshold && peaks.size() < sdr::kMaxPeaks) {
                const float bin_hz = static_cast<float>(sample_rate) / static_cast<float>(kFftSize);
                const float offset = (static_cast<float>(i) - static_cast<float>(kFftSize / 2)) * bin_hz;
                peaks.push_back({static_cast<float>(center_freq) + offset, spectrum[i]});
            }
        }

        const auto now = std::chrono::steady_clock::now();
        const float processing_latency_ms =
            std::chrono::duration<float, std::milli>(now - loop_start).count();
        const float loop_elapsed_ms =
            std::max(std::chrono::duration<float, std::milli>(now - previous_loop).count(), 0.001f);
        const float cpu_usage = std::min(100.0f, (processing_latency_ms / loop_elapsed_ms) * 100.0f);
        previous_loop = now;

        constexpr float kRingUsableCapacity = static_cast<float>(kRingCapacity - 1);
        const float buffer_fill_percent =
            (static_cast<float>(ring.size_approx()) / kRingUsableCapacity) * 100.0f;

        shm.publish(++frame_id,
                    sample_rate,
                    cfg.analog_bandwidth.load(),
                    center_freq,
                    static_cast<uint64_t>(std::time(nullptr)),
                    cfg.dropped_samples.load(std::memory_order_relaxed),
                    buffer_fill_percent,
                    processing_latency_ms,
                    cpu_usage,
                    spectrum,
                    peaks);

        for (const auto& peak : peaks) {
            std::ostringstream os;
            os << "{\"frame\":" << frame_id << ",\"freq_hz\":" << peak.freq_hz
               << ",\"power_db\":" << peak.power_db << "}";
            alert_tx.send_to(kAlertSocket, os.str());
        }
    }

    fftwf_destroy_plan(plan);
}

}  // namespace

int main() {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    RuntimeConfig cfg;
    sdr::SpscRingBuffer<SampleChunk, kRingCapacity> ring;

    std::thread producer(acquisition_thread, std::ref(ring), std::ref(cfg));
    std::thread worker(processing_thread, std::ref(ring), std::ref(cfg));
    std::thread commands(command_listener, std::ref(cfg));

    producer.join();
    worker.join();
    commands.join();
    unlink(kCmdSocket);
    unlink("/tmp/sdr_core_alert_tx.sock");
    return 0;
}
