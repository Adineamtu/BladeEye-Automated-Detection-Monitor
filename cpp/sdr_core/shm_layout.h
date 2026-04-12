#pragma once

#include <array>
#include <atomic>
#include <cstdint>

namespace sdr {

constexpr std::size_t kSpectrumBins = 2048;

struct SharedSpectrumHeader {
    std::atomic<uint32_t> state; // 0 = taken, 1 = ready
    uint64_t frame_id;
    uint32_t sample_rate;
    uint32_t analog_bandwidth;
    uint64_t center_freq;
    uint32_t peak_count;
    uint64_t last_heartbeat;
    uint32_t dropped_samples;
    float buffer_fill_percent;
    float processing_latency_ms;
    float cpu_usage;
};

struct PeakEvent {
    float freq_hz;
    float power_db;
};

constexpr std::size_t kMaxPeaks = 64;

struct SharedSpectrumFrame {
    SharedSpectrumHeader header;
    std::array<float, kSpectrumBins> spectrum_data;
    std::array<PeakEvent, kMaxPeaks> peaks;
};

}  // namespace sdr
