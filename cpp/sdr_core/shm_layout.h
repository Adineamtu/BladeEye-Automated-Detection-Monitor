#pragma once

#include <array>
#include <atomic>
#include <cstdint>

namespace sdr {

constexpr std::size_t kSpectrumBins = 2048;
constexpr std::size_t kSharedRingBytes = 128U * 1024U * 1024U;

#pragma pack(push, 1)

struct SharedSpectrumHeader {
    uint64_t frame_id;
    uint64_t center_freq;
    uint64_t last_heartbeat;
    uint32_t sample_rate;
    uint32_t analog_bandwidth;
    uint32_t peak_count;
    uint32_t dropped_samples;
    float buffer_fill_percent;
    float processing_latency_ms;
    float cpu_usage;
    uint32_t state; // 0 = free, 1 = writing, 2 = ready
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

constexpr std::size_t kSharedSlotBytes = sizeof(SharedSpectrumFrame);
constexpr std::size_t kSharedRingSlots =
    (kSharedRingBytes / kSharedSlotBytes) > 0 ? (kSharedRingBytes / kSharedSlotBytes) : 1;

struct SharedRingControl {
    uint32_t version;
    uint32_t slot_count;
    uint64_t write_seq;
    uint64_t last_committed_seq;
};

struct SharedSpectrumRingBuffer {
    SharedRingControl control;
    std::array<SharedSpectrumFrame, kSharedRingSlots> slots;
};

#pragma pack(pop)

static_assert(sizeof(SharedRingControl) == 24, "SharedRingControl layout changed unexpectedly");
static_assert(sizeof(SharedSpectrumHeader) == 56, "SharedSpectrumHeader layout changed unexpectedly");

}  // namespace sdr
