# Network evidence: Wi-Fi, NTP clock and ThingSpeak upload

To be measured with `tests/bench/bench_network.py` on the Raspberry Pi Pico W (MicroPython 1.29.0), powered over USB, connected to a 2.4 GHz phone hotspot. The files contain only times, results and states: never the network name, the password or the write key.

| File | Mode | What it shows |
|---|---|---|
| *(pending)* | `clock` | radio start time, connect, DNS and NTP times and loop stalls; UTC compared with a phone clock |
| *(pending)* | `upload` | one bulk request with 4 updates from 5 events; the re-send of the same request |
| *(pending)* | `fail_password` | a wrong password: connect gives False, and how fast |
| *(pending)* | `fail_key` | a wrong write key: ThingSpeak answers 4xx, the upload gives False |
| *(pending)* | `hotspot_off` | hotspot switched off: the upload gives None; then reconnect and upload again |

Columns: `mode,step,result,status,resp_bytes,ms,stall_ms`. `status` is the HTTP status code, `resp_bytes` the size of the response, `ms` how long the step took and `stall_ms` the worst delay of a 10 ms ticker task during that step (how long the step held up the other tasks).

Not measured yet: the board was not available on the day the code was written.
