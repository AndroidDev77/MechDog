// head/config.h - edit before flashing.
#pragma once

// ---- Wi-Fi (2.4 GHz) -------------------------------------------------------
#define WIFI_SSID      "your-wifi"
#define WIFI_PASS      "your-password"

// ---- The computer running dogbrain -----------------------------------------
#define BRAIN_HOST     "192.168.1.20"   // your Mac's IP (System Settings > Wi-Fi > Details)
#define BRAIN_PORT     8765
#define BRAIN_PATH     "/head"

// ---- Pins: ESP32-S3 dev board + INMP441 mic + NS4168/MAX98357A amp ----------
// INMP441: VDD->3V3, GND->GND, L/R->GND (left channel)
#define MIC_SCK        4
#define MIC_WS         5
#define MIC_SD         6
// NS4168 or MAX98357A: VIN->5V, GND->GND
#define SPK_BCLK       15
#define SPK_LRC        16
#define SPK_DIN        17
// Dog's 4-pin serial port: its TX -> DOG_RX, its RX -> DOG_TX, GND -> GND
#define DOG_RX         18
#define DOG_TX         8

// ---- Audio -------------------------------------------------------------------
#define SAMPLE_RATE    16000
#define MIC_GAIN_SHIFT 14      // INMP441 24-bit -> 16-bit; lower = louder (try 12-15)

// ---- Serial bridge (dogbrain transport = "tcp", address = "<this board's IP>:2323")
#define BRIDGE_PORT    2323
#define DOG_BAUD       9600
