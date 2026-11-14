// ble_link.h - Bluetooth LE serial link for MechDog.
//
// Advertises as "MechDog_XX" (XX = last byte of the MAC, the same naming as Hiwonder's stock
// firmware) with the Nordic UART Service:
//   6E400001-B5A3-F393-E0A9-E50E24DCCA9E  service
//   6E400002-...  RX: the computer writes CMD|...|$ frames here
//   6E400003-...  TX: the dog notifies replies and events here
// dogbrain (`python -m dogbrain.link --ble`) finds the dog by name and picks these
// characteristics automatically. Notifications are sent in 20-byte pieces so they arrive even
// before the MTU is negotiated; dogbrain reassembles frames at the '$'.
#pragma once
#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <freertos/stream_buffer.h>

namespace blelink {

static const char* SERVICE = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";

static StreamBufferHandle_t rx_buf = nullptr;
static BLECharacteristic* tx = nullptr;
static volatile bool connected = false;
static volatile bool just_connected = false, just_disconnected = false;
static char device_name[16];

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override {
    connected = true;
    just_connected = true;
  }
  void onDisconnect(BLEServer*) override {
    connected = false;
    just_disconnected = true;
    BLEDevice::startAdvertising();          // be findable again straight away
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    auto v = c->getValue();                 // std::string (core 2.x) or String (3.x)
    for (size_t i = 0; i < v.length(); i++) {
      char ch = v[i];
      xStreamBufferSend(rx_buf, &ch, 1, 0); // drop if the buffer is full
    }
  }
};

static void start() {
  uint64_t mac = ESP.getEfuseMac();         // bytes are little-endian: byte 5 is the last
  snprintf(device_name, sizeof(device_name), "MechDog_%02X", (uint8_t)(mac >> 40));
  rx_buf = xStreamBufferCreate(512, 1);

  BLEDevice::init(device_name);
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  BLEService* svc = server->createService(SERVICE);
  tx = svc->createCharacteristic(TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  tx->addDescriptor(new BLE2902());
  BLECharacteristic* rx = svc->createCharacteristic(
      RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  rx->setCallbacks(new RxCallbacks());
  svc->start();

  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
}

// Non-blocking: copy received bytes into buf, return how many.
static size_t read(char* buf, size_t max) {
  return rx_buf ? xStreamBufferReceive(rx_buf, buf, max, 0) : 0;
}

// Call from one task only (loop()).
static void send(const char* s) {
  if (!connected || !tx) return;
  size_t n = strlen(s);
  for (size_t i = 0; i < n; i += 20) {
    size_t k = (n - i < 20) ? n - i : 20;
    tx->setValue((uint8_t*)(s + i), k);
    tx->notify();
    delay(3);                               // let the stack drain between pieces
  }
}

}  // namespace blelink
