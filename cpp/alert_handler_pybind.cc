#include <pybind11/pybind11.h>
#include <gnuradio/pybind11/block.h>
#include "alert_handler_impl.h"

namespace py = pybind11;

void bind_alert_handler(py::module& m) {
    using gr::hackrf::alert_handler;
    py::class_<alert_handler, gr::block, alert_handler::sptr>(m, "alert_handler")
        .def(py::init(&alert_handler::make),
             py::arg("sink"),
             py::arg("selector"),
             py::arg("sweep_source"),
             py::arg("jam_duration"),
             py::arg("cooldown"),
             py::arg("dwell_time"),
             py::arg("tx_gain_db"),
             py::arg("zero_idx"),
             py::arg("noise_idx"),
             py::arg("sweep_idx"),
             py::arg("jamming_mode"),
             py::arg("jamming_bw"),
             py::arg("low_freq"),
             py::arg("high_freq"))
        .def("set_frequency", &alert_handler::set_frequency)
        .def("stop_jamming", &alert_handler::stop_jamming)
        .def("process_alert", &alert_handler::process_alert)
        .def("add_frequency", &alert_handler::add_frequency)
        .def("remove_frequency", &alert_handler::remove_frequency);
}

PYBIND11_MODULE(_alert_handler, m) { bind_alert_handler(m); }
