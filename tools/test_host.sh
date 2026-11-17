#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/host
for test in protocol product app; do
    sources=(uart_to_can/App/Src/bridge_protocol.c)
    extra=()
    if [[ "$test" == product ]]; then
        sources+=(uart_to_can/App/Src/bridge_store.c uart_to_can/App/Src/bridge_ui.c)
    fi
    if [[ "$test" == app ]]; then
        sources+=(uart_to_can/App/Src/bridge_ui.c)
        extra=(-Itests/hal_stubs -Wno-unused-parameter -ffunction-sections -fdata-sections -Wl,--gc-sections)
    fi
    gcc -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -fno-omit-frame-pointer -no-pie \
        -Iuart_to_can/App/Inc "${extra[@]}" "tests/test_${test}.c" "${sources[@]}" -o "build/host/test_${test}"
    "build/host/test_${test}"
done
python3 -m unittest discover -s tests -p 'test_*.py' -v
