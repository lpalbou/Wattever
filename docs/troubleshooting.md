# Troubleshooting

- **Hosted page cannot connect:** check the endpoint URL, server availability, CORS and your browser's local-network permission. HTTPS-to-localhost behavior varies by browser. See [browser mode](browser.md).
- **Browser storage warning:** download the result JSON before closing the page; an in-memory result is lost on reload.
- **Helper timeout recovery stopped:** inspect LM Studio and ensure the model and requests have stopped before starting another run. See [measurements](measurements.md).

- **LM Studio or models unavailable:** install LM Studio, run `lms bootstrap`, start its server and check that the models are downloaded. Verify `LMS_BASE_URL` if using a different endpoint. Reopen Wattever and check the Run page.
- **Power unavailable:** check `nvidia-smi` for NVIDIA sensors. On macOS, use the launcher and accept the optional power prompt; without authorization speed tests still work. Other sensors may not provide power. See [measurements](measurements.md).
- **Load failure or context overflow:** reduce prompt size or simultaneous queries, inspect model context limits and available memory, or choose a smaller model. A failure is retained in results; use the retry option after correcting the cause.
- **Extension rejected:** hardware, selected GPU and driver, corpus and LM Studio version must match. Start a separate benchmark if they changed or cannot be verified. See [sessions](measurements.md).
- **No response from Wattever:** check the URL printed by its launcher and keep the server window open. The default starting port is 8713 but another available port may be selected. See [getting started](getting-started.md).

- **File-save warning:** update and restart the desktop helper, then use **Add / rerun tests ? Add missing tests** to continue. Completed records remain saved. The warning?s **Technical details** disclosure contains the original diagnostic.
