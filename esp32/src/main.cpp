#include <Arduino.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <driver/i2s.h>

#include "secrets.h"

namespace {
constexpr i2s_port_t kI2sPort = I2S_NUM_0;
constexpr int kSampleRate = 16000;
constexpr int kBlockSamples = 512;
constexpr int kSckPin = 32;
constexpr int kWsPin = 25;
constexpr int kSdPin = 33;
constexpr float kTargetHz = 4105.0F;
constexpr float kMinimumToneRms = 18.0F;
constexpr float kMinimumToneRatio = 5.0F;
constexpr uint32_t kMinimumBeepMs = 120;
constexpr uint32_t kMaximumBeepMs = 600;
constexpr uint32_t kMinimumStartGapMs = 550;
constexpr uint32_t kMaximumStartGapMs = 1450;
constexpr uint32_t kAlertCooldownMs = 60000;

Preferences preferences;
int32_t raw_samples[kBlockSamples];
bool tone_active = false;
uint32_t tone_started_ms = 0;
uint32_t previous_beep_ms = 0;
uint32_t last_alert_ms = 0;
unsigned beep_count = 0;

float goertzelPower(const int16_t *samples, float frequency) {
  const float omega = 2.0F * PI * frequency / kSampleRate;
  const float coefficient = 2.0F * cosf(omega);
  float previous = 0.0F;
  float previous2 = 0.0F;
  for (int i = 0; i < kBlockSamples; ++i) {
    const float next = static_cast<float>(samples[i]) + coefficient * previous - previous2;
    previous2 = previous;
    previous = next;
  }
  return previous2 * previous2 + previous * previous - coefficient * previous * previous2;
}

bool hasCoffeeTone(const int16_t *samples, float *rms_out, float *ratio_out) {
  uint64_t sum_squares = 0;
  for (int i = 0; i < kBlockSamples; ++i) {
    const int32_t sample = samples[i];
    sum_squares += static_cast<uint64_t>(sample * sample);
  }
  const float rms = sqrtf(static_cast<float>(sum_squares) / kBlockSamples);
  const float target = goertzelPower(samples, kTargetHz);
  const float background = (goertzelPower(samples, 3500.0F) +
                            goertzelPower(samples, 4700.0F)) * 0.5F;
  const float ratio = target / fmaxf(background, 1.0F);
  *rms_out = rms;
  *ratio_out = ratio;
  return rms >= kMinimumToneRms && ratio >= kMinimumToneRatio;
}

bool sendPushover() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("alert failed: Wi-Fi is not connected");
    return false;
  }
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  if (!http.begin(client, "https://api.pushover.net/1/messages.json")) {
    Serial.println("alert failed: HTTPS setup failed");
    return false;
  }
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  const String body = "token=" + String(PUSHOVER_COFFEE_TOKEN) +
                      "&user=" + String(PUSHOVER_USER) +
                      "&title=Coffee%20roaster&message=The%20coffee%20roaster%20is%20warmed%20up%20and%20ready%20for%20beans.";
  const int status = http.POST(body);
  http.end();
  Serial.printf("Pushover response: %d\n", status);
  return status == 200;
}

void recordBeep(uint32_t started_ms) {
  if (previous_beep_ms != 0) {
    const uint32_t gap = started_ms - previous_beep_ms;
    if (gap < kMinimumStartGapMs || gap > kMaximumStartGapMs) {
      beep_count = 0;
    }
  }
  previous_beep_ms = started_ms;
  ++beep_count;
  Serial.printf("coffee tone %u/3\n", beep_count);
  if (beep_count < 3) {
    return;
  }
  beep_count = 0;
  previous_beep_ms = 0;
  const uint32_t now = millis();
  if (last_alert_ms != 0 && now - last_alert_ms < kAlertCooldownMs) {
    Serial.println("cadence detected; alert is in cooldown");
    return;
  }
  Serial.println("coffee warm-up cadence detected");
  if (sendPushover()) {
    last_alert_ms = now;
  }
}

void observeTone(bool present, uint32_t now_ms) {
  if (present && !tone_active) {
    tone_active = true;
    tone_started_ms = now_ms;
  } else if (!present && tone_active) {
    tone_active = false;
    const uint32_t duration = now_ms - tone_started_ms;
    if (duration >= kMinimumBeepMs && duration <= kMaximumBeepMs) {
      recordBeep(tone_started_ms);
    }
  }
}

void configureI2s() {
  const i2s_config_t config = {
      .mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = kSampleRate,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 4,
      .dma_buf_len = kBlockSamples,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
      .mclk_multiple = I2S_MCLK_MULTIPLE_DEFAULT,
      .bits_per_chan = I2S_BITS_PER_CHAN_DEFAULT,
  };
  const i2s_pin_config_t pins = {
      .mck_io_num = I2S_PIN_NO_CHANGE,
      .bck_io_num = kSckPin,
      .ws_io_num = kWsPin,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = kSdPin,
  };
  ESP_ERROR_CHECK(i2s_driver_install(kI2sPort, &config, 0, nullptr));
  ESP_ERROR_CHECK(i2s_set_pin(kI2sPort, &pins));
}

void connectWifi() {
  preferences.begin("coffee", false);
  const String ssid = preferences.getString("ssid", "");
  const String password = preferences.getString("password", "");
  if (ssid.isEmpty()) {
    Serial.println("Wi-Fi is not set. Send: WIFI your-ssid|your-password");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), password.c_str());
  Serial.print("connecting to Wi-Fi");
  for (unsigned i = 0; i < 40 && WiFi.status() != WL_CONNECTED; ++i) {
    delay(250);
    Serial.print('.');
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected" : " failed");
}

void processSerial() {
  if (!Serial.available()) {
    return;
  }
  String command = Serial.readStringUntil('\n');
  command.trim();
  if (!command.startsWith("WIFI ")) {
    Serial.println("unknown command");
    return;
  }
  const int separator = command.indexOf('|', 5);
  if (separator < 6) {
    Serial.println("use: WIFI your-ssid|your-password");
    return;
  }
  preferences.putString("ssid", command.substring(5, separator));
  preferences.putString("password", command.substring(separator + 1));
  Serial.println("Wi-Fi saved; restarting");
  delay(200);
  ESP.restart();
}
}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(1000);
  delay(500);
  Serial.println("coffee detector starting");
  connectWifi();
  configureI2s();
}

void loop() {
  processSerial();
  size_t bytes_read = 0;
  if (i2s_read(kI2sPort, raw_samples, sizeof(raw_samples), &bytes_read,
               pdMS_TO_TICKS(1000)) != ESP_OK) {
    Serial.println("I2S read failed");
    return;
  }
  static int16_t samples[kBlockSamples];
  const int count = bytes_read / sizeof(raw_samples[0]);
  for (int i = 0; i < count; ++i) {
    samples[i] = static_cast<int16_t>(raw_samples[i] >> 16);
  }
  for (int i = count; i < kBlockSamples; ++i) {
    samples[i] = 0;
  }
  float rms = 0.0F;
  float ratio = 0.0F;
  const bool tone = hasCoffeeTone(samples, &rms, &ratio);
  observeTone(tone, millis());
  static uint32_t last_status = 0;
  if (millis() - last_status >= 2000) {
    Serial.printf("mic rms=%.1f tone_ratio=%.1f wifi=%s\n", rms, ratio,
                  WiFi.status() == WL_CONNECTED ? "ok" : "offline");
    last_status = millis();
  }
}
