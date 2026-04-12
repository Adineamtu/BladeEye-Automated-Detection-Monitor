#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <optional>

namespace sdr {

template <typename T, std::size_t Capacity>
class SpscRingBuffer {
public:
    bool push(const T& value) {
        const auto head = head_.load(std::memory_order_relaxed);
        const auto next = increment(head);
        if (next == tail_.load(std::memory_order_acquire)) {
            return false;
        }
        data_[head] = value;
        head_.store(next, std::memory_order_release);
        return true;
    }

    std::optional<T> pop() {
        const auto tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) {
            return std::nullopt;
        }
        T out = data_[tail];
        tail_.store(increment(tail), std::memory_order_release);
        return out;
    }

    std::size_t size_approx() const {
        const auto head = head_.load(std::memory_order_acquire);
        const auto tail = tail_.load(std::memory_order_acquire);
        if (head >= tail) {
            return head - tail;
        }
        return Capacity - (tail - head);
    }

private:
    static constexpr std::size_t increment(std::size_t idx) { return (idx + 1) % Capacity; }

    std::array<T, Capacity> data_{};
    std::atomic<std::size_t> head_{0};
    std::atomic<std::size_t> tail_{0};
};

}  // namespace sdr
