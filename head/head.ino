// head/head.ino - firmware for the ESP32-S3 audio board on MechDog's head.
//
// Does three jobs:
//   1. Streams the INMP441 microphone to dogbrain over WebSocket
//      (binary frames, 16 kHz 16-bit mono PCM, 20 ms each)
//   2. Plays audio that dogbrain sends back (same format) on the NS4168/MAX98357A amp
//      Text frames from dogbrain: "stop" = flush playback, "vol:0.0-1.0" = volume
//   3. Bridges the dog's serial port to TCP port 2323, so dogbrain can drive the dog
//      over Wi-Fi with the same CMD|...|$ protocol (transport = "tcp")
//
// Build: Arduino IDE 2.x, board "ESP32S3 Dev Module" (esp32 core 3.x),
//        PSRAM "OPI PSRAM" if your board has it.
// Library: "WebSockets" by Markus Sattler (Library Manager).
// Edit config.h first.

#include <WiFi.h>
#include <ESP_I2S.h>
#include <WebSocketsClient.h>
#include <freertos/stream_buffer.h>
#include "config.h"

static const size_t FRAME_SAMPLES = SAMPLE_RATE / 50;     // 20 ms
static const size_t PLAY_BUF_BYTES = 64 * 1024;           // ~2 s of audio

I2SClass mic;
I2SClass spk;
WebSocketsClient ws;
WiFiServer bridge(BRIDGE_PORT);
WiFiClient bridgeClient;
StreamBufferHandle_t playBuf;
StreamBufferHandle_t micBuf;      // mic task -> loop(); the WebSocket client is not thread-safe

volatile bool wsConnected = false;
volatile float volume = 0.8f;

// ---------------------------------------------------------------------------
void onWs(WStype_t type, uint8_t *payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED:
      wsConnected = true;
      ws.sendTXT("hello head v1 rate=16000");
      Serial.println("[ws] connected");
      break;
    case WStype_DISCONNECTED:
      wsConnected = false;
      xStreamBufferReset(playBuf);
      Serial.println("[ws] disconnected");
      break;
    case WStype_BIN:
      // Wait up to 50 ms for space; dogbrain paces sends at real time.
      xStreamBufferSend(playBuf, payload, len, pdMS_TO_TICKS(50));
      break;
    case WStype_TEXT: {
      String msg((const char *)payload, len);
      if (msg == "stop") {
        xStreamBufferReset(playBuf);
      } else if (msg.startsWith("vol:")) {
        volume = constrain(msg.substring(4).toFloat(), 0.0f, 1.0f);
      }
      break;
    }
    default:
      break;
  }
}

// ---------------------------------------------------------------------------
void micTask(void *) {
  static int32_t raw[FRAME_SAMPLES];
  static int16_t pcm[FRAME_SAMPLES];
  for (;;) {
    size_t got = mic.readBytes((char *)raw, sizeof(raw));
    size_t n = got / sizeof(int32_t);
    for (size_t i = 0; i < n; i++) {
      int32_t s = raw[i] >> MIC_GAIN_SHIFT;
      pcm[i] = (int16_t)constrain(s, -32768, 32767);
    }
    if (wsConnected && n) {
      xStreamBufferSend(micBuf, pcm, n * sizeof(int16_t), 0);   // drop if loop() falls behind
    }
  }
}

void speakerTask(void *) {
  static int16_t buf[256];
  static int16_t silence[64] = {0};
  for (;;) {
    size_t got = xStreamBufferReceive(playBuf, buf, sizeof(buf), pdMS_TO_TICKS(20));
    if (got) {
      size_t n = got / sizeof(int16_t);
      float v = volume;
      for (size_t i = 0; i < n; i++) buf[i] = (int16_t)(buf[i] * v);
      spk.write((uint8_t *)buf, n * sizeof(int16_t));
    } else {
      spk.write((uint8_t *)silence, sizeof(silence));   // keep the amp from popping
    }
  }
}

// ---------------------------------------------------------------------------
void bridgeLoop() {
  if (!bridgeClient || !bridgeClient.connected()) {
    WiFiClient c = bridge.accept();
    if (c) {
      bridgeClient = c;
      bridgeClient.setNoDelay(true);
      Serial.println("[bridge] client connected");
    }
  }
  if (bridgeClient && bridgeClient.connected()) {
    uint8_t b[128];
    int n;
    while ((n = bridgeClient.read(b, sizeof(b))) > 0) Serial1.write(b, n);
    while ((n = Serial1.available()) > 0) {
      n = Serial1.readBytes(b, min(n, (int)sizeof(b)));
      bridgeClient.write(b, n);
    }
  } else {
    while (Serial1.available()) Serial1.read();   // drop dog chatter with no listener
  }
}

// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial1.begin(DOG_BAUD, SERIAL_8N1, DOG_RX, DOG_TX);

  playBuf = xStreamBufferCreate(PLAY_BUF_BYTES, 1);
  micBuf = xStreamBufferCreate(FRAME_SAMPLES * sizeof(int16_t) * 10, 1);

  mic.setPins(MIC_SCK, MIC_WS, -1, MIC_SD);
  if (!mic.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO,
                 I2S_STD_SLOT_LEFT)) {
    Serial.println("mic init failed");
  }
  spk.setPins(SPK_BCLK, SPK_LRC, SPK_DIN, -1);
  if (!spk.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO,
                 I2S_STD_SLOT_LEFT)) {
    Serial.println("speaker init failed");
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);              // lower latency
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.printf("\nIP %s  (bridge on port %d)\n", WiFi.localIP().toString().c_str(), BRIDGE_PORT);

  bridge.begin();
  ws.begin(BRAIN_HOST, BRAIN_PORT, BRAIN_PATH);
  ws.onEvent(onWs);
  ws.setReconnectInterval(2000);
  ws.enableHeartbeat(5000, 2000, 2);

  xTaskCreatePinnedToCore(micTask, "mic", 8192, nullptr, 5, nullptr, 1);
  xTaskCreatePinnedToCore(speakerTask, "spk", 8192, nullptr, 5, nullptr, 1);
}

void loop() {
  static int16_t frame[FRAME_SAMPLES];
  static uint32_t lastWifiTry = 0;

  ws.loop();
  while (wsConnected && xStreamBufferBytesAvailable(micBuf) >= sizeof(frame)) {
    xStreamBufferReceive(micBuf, frame, sizeof(frame), 0);
    ws.sendBIN((uint8_t *)frame, sizeof(frame));
  }
  bridgeLoop();
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 5000) {
    lastWifiTry = millis();
    WiFi.reconnect();
  }
  delay(1);
}
