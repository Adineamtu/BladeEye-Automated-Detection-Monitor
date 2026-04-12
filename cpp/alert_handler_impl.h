#pragma once

#include <gnuradio/block.h>
#include <osmosdr/sink.h>
#include <gnuradio/blocks/selector.h>
#include <gnuradio/analog/sig_source_c.h>
#include <atomic>
#include <thread>
#include <set>
#include <mutex>

namespace gr {
namespace hackrf {

class alert_handler : public gr::block {
public:
    typedef std::shared_ptr<alert_handler> sptr;
    static sptr make(osmosdr::sink::sptr sink,
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
                     double high_freq);

    void set_frequency(double freq);
    void stop_jamming();
    void process_alert(double freq);
    void add_frequency(double freq);
    void remove_frequency(double freq);

    ~alert_handler();

private:
    alert_handler(osmosdr::sink::sptr sink,
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
                  double high_freq);

    void handle_msg(pmt::pmt_t msg);
    void jam_once(double freq);
    void rapid_hop_jammer_loop();

    osmosdr::sink::sptr d_sink;
    gr::blocks::selector::sptr d_selector;
    gr::analog::sig_source_c::sptr d_sweep;
    double d_jam_duration;
    double d_cooldown;
    double d_tx_gain;
    double d_dwell_time;
    int d_zero_idx;
    int d_noise_idx;
    int d_sweep_idx;
    std::string d_mode;
    double d_bw;
    double d_freq;
    double d_low_freq;
    double d_high_freq;
    std::set<double> d_target_freqs;
    std::mutex d_freq_mutex;
    std::atomic<bool> d_stop_thread;
    std::thread d_worker;
};

} // namespace hackrf
} // namespace gr

