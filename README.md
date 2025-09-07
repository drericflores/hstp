# Hardware Stress Testing Tool (Python Edition)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)]()

A **Tkinter-based GUI application** for system stress testing and monitoring.  
Developed by **Dr. Eric O. Flores** as the Python counterpart to the C++/Qt version.

---

## ✨ Features

- **Stress tests**:
  - CPU & RAM via [`stress-ng`](https://manpages.ubuntu.com/manpages/jammy/en/man1/stress-ng.1.html)
  - GPU via [`glmark2`](https://github.com/glmark2/glmark2)
  - Disk via [`fio`](https://github.com/axboe/fio)
  - Network via [`iperf3`](https://iperf.fr/)
- **Live output panel** with scrolling logs
- **Start / Stop / Clear** controls
- **Progress bar with ETA**
- **Automatic logging** to `~/HardwareStressTest/logs`
- **Dashboard gauges** (CPU, Memory, Disk) powered by [`psutil`](https://pypi.org/project/psutil/)
- **Light/Dark mode themes** switchable in Tools menu

---

## 📦 Requirements

- Python ≥ 3.10
- Tkinter (usually included with Python)
- [`psutil`](https://pypi.org/project/psutil/)

Install system tools for testing:
```bash
sudo apt install stress-ng glmark2 fio iperf3
