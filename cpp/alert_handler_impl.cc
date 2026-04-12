#include "alert_handler_impl.h"
#include <pmt/pmt.h>
#include <chrono>
#include <iostream>
#include <vector>

namespace gr {
namespace hackrf {

alert_handler::sptr alert_handler::make(osmosdr::sink::sptr sink,
                                        gr::blocks::selector::sptr selector,
                                        gr::analog::sig_source_c::sptr sweep,
                                        double jam_duration,
                                        double cooldown,
                                        double dwell_time,
                                        double tx_gain,
                                        int zero_idx,
                                        int noise_idx,
                                        int sweep_idx,
                                        const std::string& mode,
                                        double jamming_bw,
                                        double low_freq,
                                        double high_freq)
{
    return sptr(new alert_handler(sink,
                                 selector,
                                 sweep,
                                 jam_duration,
                                 cooldown,
                                 dwell_time,
                                 tx_gain,
                                 zero_idx,
                                 noise_idx,
                                 sweep_idx,
                                 mode,
                                 jamming_bw,
                                 low_freq,
                                 high_freq));
}

alert_handler::alert_handler(osmosdr::sink::sptr sink,
                             gr::blocks::selector::sptr selector,
                             gr::analog::sig_source_c::sptr sweep,
                             double jam_duration,
                             double cooldown,
                             double dwell_time,
                             double tx_gain,
                             int zero_idx,
                             int noise_idx,
                             int sweep_idx,
                             const std::string& mode,
                             double jamming_bw,
                             double low_freq,
                             double high_freq)
    : gr::block("alert_handler", gr::io_signature::make(0, 0, 0),
                gr::io_signature::make(0, 0, 0)),
      d_sink(sink), d_selector(selector), d_sweep(sweep),
      d_jam_duration(jam_duration), d_cooldown(cooldown),
      d_tx_gain(tx_gain), d_dwell_time(dwell_time), d_zero_idx(zero_idx),
      d_noise_idx(noise_idx), d_sweep_idx(sweep_idx), d_mode(mode),
      d_bw(jamming_bw), d_freq(0.0), d_low_freq(low_freq),
      d_high_freq(high_freq), d_stop_thread(false)
{
    message_port_register_in(pmt::mp("alert"));
    set_msg_handler(pmt::mp("alert"), [this](pmt::pmt_t msg) { handle_msg(msg); });
}

void alert_handler::set_frequency(double freq) { d_freq = freq; }

alert_handler::~alert_handler() { stop_jamming(); }

void alert_handler::handle_msg(pmt::pmt_t msg)
{
    double freq = d_freq;
    if (pmt::is_real(msg) || pmt::is_integer(msg))
        freq = pmt::to_double(msg);
    process_alert(freq);
}

void alert_handler::jam_once(double freq)
{
    if (freq > 0)
        d_sink->set_center_freq(freq);
    int idx = d_noise_idx;
    if (d_mode == "SWEEP" && d_sweep && d_sweep_idx >= 0) {
        if (d_bw > 0)
            d_sweep->set_frequency(d_bw / 2.0);
        idx = d_sweep_idx;
    }
    if (idx >= 0) {
        d_sink->set_gain(d_tx_gain, 0);
        d_selector->set_input_index(idx);
    }
}

void alert_handler::rapid_hop_jammer_loop()
{
    while (!d_stop_thread.load()) {
        std::vector<double> freqs;
        {
            std::lock_guard<std::mutex> lock(d_freq_mutex);
            freqs.assign(d_target_freqs.begin(), d_target_freqs.end());
        }
        if (freqs.empty())
            break;
        for (double f : freqs) {
            if (d_stop_thread.load())
                break;
            jam_once(f);
            std::this_thread::sleep_for(
                std::chrono::duration<double>(d_dwell_time));
        }
    }
    d_sink->set_gain(0, 0);
    d_selector->set_input_index(d_zero_idx);
}

void alert_handler::add_frequency(double freq)
{
    bool start_thread = false;
    {
        std::lock_guard<std::mutex> lock(d_freq_mutex);
        auto inserted = d_target_freqs.insert(freq).second;
        start_thread = inserted && !d_worker.joinable();
    }
    if (start_thread) {
        d_stop_thread.store(false);
        d_worker = std::thread(&alert_handler::rapid_hop_jammer_loop, this);
    }
}

void alert_handler::remove_frequency(double freq)
{
    bool should_stop = false;
    {
        std::lock_guard<std::mutex> lock(d_freq_mutex);
        d_target_freqs.erase(freq);
        should_stop = d_target_freqs.empty();
    }
    if (should_stop) {
        d_stop_thread.store(true);
        if (d_worker.joinable())
            d_worker.join();
        d_stop_thread.store(false);
        d_sink->set_gain(0, 0);
        d_selector->set_input_index(d_zero_idx);
    }
}

void alert_handler::process_alert(double freq)
{
    if (freq < d_low_freq || freq > d_high_freq) {
        std::clog << "Ignoring alert at " << freq << " Hz outside [" << d_low_freq
                  << ", " << d_high_freq << "]" << std::endl;
        return;
    }

    bool already = false;
    {
        std::lock_guard<std::mutex> lock(d_freq_mutex);
        already = d_target_freqs.count(freq) != 0;
    }
    if (already)
        remove_frequency(freq);
    else
        add_frequency(freq);
}

void alert_handler::stop_jamming()
{
    d_stop_thread.store(true);
    if (d_worker.joinable())
        d_worker.join();
    d_stop_thread.store(false);
    {
        std::lock_guard<std::mutex> lock(d_freq_mutex);
        d_target_freqs.clear();
    }
    d_sink->set_gain(0, 0);
    d_selector->set_input_index(d_zero_idx);
}

} // namespace hackrf
} // namespace gr

