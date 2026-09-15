#include <Adafruit_Thermal.h>
#include <Arduino_LED_Matrix.h>
#include <Arduino_Modulino.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;
ModulinoButtons buttons;
ModulinoPixels pixels;
Adafruit_Thermal printer(&Serial1);

enum CameraState { READY, COUNTDOWN, CAPTURE, GENERATING, PRINTING, DONE, ERROR_STATE, WIFI_SCAN, WIFI_CONNECT, WIFI_SUCCESS, WIFI_ERROR };
CameraState cameraState = READY;
bool previousPressed[3] = {false, false, false};
bool optionsAvailable[6] = {false, false, false, false, false, false};
bool pressArmed[3] = {false, false, false};
unsigned long pressStartedAt[3] = {0, 0, 0};
bool wifiChordActive = false;
bool wifiChordTriggered = false;
unsigned long wifiChordStartedAt = 0;
bool printJobActive = false;
uint16_t printRowsExpected = 0;
uint16_t printRowsReceived = 0;
int8_t activeMode = -1;
unsigned long lastFrameAt = 0;
unsigned long captureFlashUntil = 0;
uint8_t animationFrame = 0;

void drawState();
void updateControls();
void updatePixels();
void setActiveMode(int mode);
void setOptions(int availabilityMask);
void setCameraStatus(String status);
bool setCaptureLight(bool enabled);
bool beginPrint(uint16_t rows);
bool appendPrintRows(std::vector<int> packedRows);
bool endPrint(uint8_t feedLines);
bool cancelPrint();
bool configurePrinter(uint8_t heatDots, uint8_t heatTime, uint8_t heatInterval, uint8_t density, uint8_t breakTime);
bool testPrint();
bool printWifiTicket(String title, String ssid, String psk, String message);

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  Serial1.begin(9600);
  printer.begin();
  Bridge.begin();
  Bridge.provide("camera_status", setCameraStatus);
  Bridge.provide("capture_light", setCaptureLight);
  Bridge.provide("print_begin", beginPrint);
  Bridge.provide("print_rows", appendPrintRows);
  Bridge.provide("print_end", endPrint);
  Bridge.provide("print_cancel", cancelPrint);
  Bridge.provide("print_configure", configurePrinter);
  Bridge.provide("print_test", testPrint);
  Bridge.provide("print_wifi_ticket", printWifiTicket);
  Modulino.begin(Wire1);
  buttons.begin();
  pixels.begin();
  pixels.show();
  Bridge.provide("set_active_mode", setActiveMode);
  Bridge.provide("set_options", setOptions);
  drawState();
  updateControls();
}

void loop() {
  if (buttons.update()) {
    const char *profiles[6] = {
      "a_short", "b_short", "c_short",
      "a_long", "b_long", "c_long"
    };
    for (int index = 0; index < 3; index++) {
      bool pressed = buttons.isPressed(index) == HIGH;
      if (pressed && !previousPressed[index] && cameraState != READY) {
        printJobActive = false;
        printRowsExpected = 0;
        printRowsReceived = 0;
        captureFlashUntil = 0;
        cameraState = READY;
        activeMode = -1;
        drawState();
        updateControls();
        Bridge.notify("cancel_process");
      } else if (pressed && !previousPressed[index] && cameraState == READY) {
        pressArmed[index] = true;
        pressStartedAt[index] = millis();
      } else if (!pressed && previousPressed[index] && pressArmed[index] && cameraState == READY) {
        bool longPress = millis() - pressStartedAt[index] >= 900;
        int profileIndex = index + (longPress ? 3 : 0);
        if (optionsAvailable[profileIndex]) {
          Bridge.notify("take_photo", profiles[profileIndex]);
        }
        pressArmed[index] = false;
      } else if (!pressed) {
        pressArmed[index] = false;
      }
      previousPressed[index] = pressed;
    }
  }

  if (cameraState == READY && previousPressed[0] && previousPressed[2]) {
    if (!wifiChordActive) {
      wifiChordActive = true;
      wifiChordStartedAt = millis();
    } else if (!wifiChordTriggered && millis() - wifiChordStartedAt >= 1500) {
      wifiChordTriggered = true;
      pressArmed[0] = false;
      pressArmed[2] = false;
      Bridge.notify("wifi_setup");
    }
  } else {
    wifiChordActive = false;
    wifiChordTriggered = false;
  }

  if ((cameraState == COUNTDOWN || cameraState == GENERATING || cameraState == PRINTING || cameraState == WIFI_SCAN || cameraState == WIFI_CONNECT) && millis() - lastFrameAt > 160) {
    lastFrameAt = millis();
    animationFrame = (animationFrame + 1) % 8;
    drawState();
    updateControls();
  }
  if (captureFlashUntil && millis() >= captureFlashUntil) {
    captureFlashUntil = 0;
    updateControls();
  }
  delay(15);
}

void setOptions(int availabilityMask) {
  for (int index = 0; index < 6; index++) {
    optionsAvailable[index] = availabilityMask & (1 << index);
  }
  updateControls();
}

void setActiveMode(int mode) {
  activeMode = mode;
  updateControls();
}

void setCameraStatus(String status) {
  if (status == "ready") cameraState = READY;
  else if (status == "countdown") cameraState = COUNTDOWN;
  else if (status == "capture") cameraState = CAPTURE;
  else if (status == "generating") cameraState = GENERATING;
  else if (status == "printing") cameraState = PRINTING;
  else if (status == "done") cameraState = DONE;
  else if (status == "wifi_scan") cameraState = WIFI_SCAN;
  else if (status == "wifi_connect") cameraState = WIFI_CONNECT;
  else if (status == "wifi_success") cameraState = WIFI_SUCCESS;
  else if (status == "wifi_error") cameraState = WIFI_ERROR;
  else cameraState = ERROR_STATE;
  animationFrame = 0;
  drawState();
  updateControls();
}

bool setCaptureLight(bool enabled) {
  if (enabled) {
    for (int index = 0; index < 8; index++) pixels.set(index, WHITE, 100);
    pixels.show();
    captureFlashUntil = millis() + 20000;
  } else {
    captureFlashUntil = 0;
    updateControls();
  }
  return true;
}

bool beginPrint(uint16_t rows) {
  if (printJobActive || rows == 0 || rows > 2048) return false;
  printJobActive = true;
  printRowsExpected = rows;
  printRowsReceived = 0;
  cameraState = PRINTING;
  animationFrame = 0;
  drawState();
  updateControls();
  return true;
}

bool appendPrintRows(std::vector<int> packedRows) {
  constexpr size_t bytesPerRow = 48;
  constexpr size_t maxRowsPerChunk = 8;
  if (!printJobActive || packedRows.empty() || packedRows.size() % bytesPerRow != 0) return false;
  size_t rows = packedRows.size() / bytesPerRow;
  if (rows > maxRowsPerChunk || printRowsReceived + rows > printRowsExpected) return false;

  uint8_t bitmap[bytesPerRow * maxRowsPerChunk];
  for (size_t index = 0; index < packedRows.size(); index++) {
    if (packedRows[index] < 0 || packedRows[index] > 255) return false;
    bitmap[index] = static_cast<uint8_t>(packedRows[index]);
  }
  printer.printBitmap(384, rows, bitmap, false);
  printRowsReceived += rows;
  return true;
}

bool endPrint(uint8_t feedLines) {
  if (!printJobActive || printRowsReceived != printRowsExpected) return false;
  printer.feed(min(feedLines, static_cast<uint8_t>(8)));
  printJobActive = false;
  cameraState = DONE;
  drawState();
  updateControls();
  return true;
}

bool cancelPrint() {
  printJobActive = false;
  printRowsExpected = 0;
  printRowsReceived = 0;
  cameraState = ERROR_STATE;
  drawState();
  updateControls();
  return true;
}

bool configurePrinter(uint8_t heatDots, uint8_t heatTime, uint8_t heatInterval, uint8_t density, uint8_t breakTime) {
  if (printJobActive || heatDots < 1 || heatDots > 30 || heatTime < 3 || density > 20 || breakTime > 7) return false;
  printer.setHeatConfig(heatDots, heatTime, heatInterval);
  printer.setPrintDensity(breakTime, density);
  return true;
}

bool testPrint() {
  if (printJobActive) return false;
  cameraState = PRINTING;
  drawState();
  updateControls();
  printer.justify('C');
  printer.boldOn();
  printer.println("VENTUNO Q AI CAMERA");
  printer.boldOff();
  printer.println("Printer test OK");
  printer.feed(3);
  cameraState = DONE;
  drawState();
  updateControls();
  return true;
}

bool printWifiTicket(String title, String ssid, String psk, String message) {
  if (printJobActive) return false;
  printer.justify('C');
  printer.boldOn();
  printer.println(title);
  printer.boldOff();
  printer.justify('L');
  if (ssid.length()) {
    printer.print("SSID: ");
    printer.println(ssid);
  }
  if (psk.length()) {
    printer.print("PSK: ");
    printer.println(psk);
  }
  if (message.length()) printer.println(message);
  printer.feed(2);
  return true;
}

void updateControls() {
  if (cameraState == READY) {
    buttons.setLeds(
      optionsAvailable[0] || optionsAvailable[3],
      optionsAvailable[1] || optionsAvailable[4],
      optionsAvailable[2] || optionsAvailable[5]
    );
    updatePixels();
    return;
  }
  bool flashOn = animationFrame % 2 == 0;
  if (cameraState == WIFI_SCAN || cameraState == WIFI_CONNECT || cameraState == WIFI_SUCCESS || cameraState == WIFI_ERROR) {
    buttons.setLeds(flashOn, false, flashOn);
    updatePixels();
    return;
  }
  buttons.setLeds(
    activeMode == 0 && flashOn,
    activeMode == 1 && flashOn,
    activeMode == 2 && flashOn
  );
  updatePixels();
}

void updatePixels() {
  if (cameraState == CAPTURE && captureFlashUntil) return;
  pixels.clear();
  if (cameraState == WIFI_SCAN) {
    for (int index = 0; index < 8; index++) pixels.set(index, WHITE, 55);
  } else if (cameraState == WIFI_CONNECT) {
    pixels.set(animationFrame, 35, 190, 190, 35);
    pixels.set((animationFrame + 7) % 8, 20, 80, 80, 16);
  } else if (cameraState == WIFI_SUCCESS) {
    for (int index = 0; index < 8; index++) pixels.set(index, 35, 180, 95, 25);
  } else if (cameraState == WIFI_ERROR) {
    for (int index = 0; index < 8; index++) pixels.set(index, 220, 45, 35, 25);
  } else if (cameraState == COUNTDOWN || cameraState == GENERATING) {
    uint8_t red = activeMode == 2 ? 230 : 45;
    uint8_t green = activeMode == 0 ? 180 : (activeMode == 2 ? 155 : 125);
    uint8_t blue = activeMode == 1 ? 220 : 45;
    pixels.set(animationFrame, red, green, blue, 35);
  } else if (cameraState == PRINTING) {
    pixels.set(animationFrame, 35, 190, 190, 35);
    pixels.set((animationFrame + 7) % 8, 20, 80, 80, 16);
  } else if (cameraState == DONE) {
    for (int index = 0; index < 8; index++) pixels.set(index, 35, 180, 95, 25);
  } else if (cameraState == ERROR_STATE) {
    for (int index = 0; index < 8; index++) pixels.set(index, 220, 45, 35, 25);
  }
  pixels.show();
}

void drawState() {
  uint8_t frame[104] = {0};
  if (cameraState == READY) {
    for (int x = 3; x < 10; x++) { frame[2 * 13 + x] = 3; frame[6 * 13 + x] = 3; }
    for (int y = 2; y < 7; y++) { frame[y * 13 + 3] = 3; frame[y * 13 + 9] = 3; }
    frame[3 * 13 + 6] = 5; frame[4 * 13 + 5] = 5; frame[4 * 13 + 6] = 7;
    frame[4 * 13 + 7] = 5; frame[5 * 13 + 6] = 5;
  } else if (cameraState == WIFI_SCAN) {
    for (int x = 1; x < 5; x++) { frame[1 * 13 + x] = 4; frame[6 * 13 + x] = 4; }
    for (int x = 8; x < 12; x++) { frame[1 * 13 + x] = 4; frame[6 * 13 + x] = 4; }
    for (int y = 1; y < 4; y++) { frame[y * 13 + 1] = 4; frame[y * 13 + 11] = 4; }
    for (int y = 4; y < 7; y++) { frame[y * 13 + 1] = 4; frame[y * 13 + 11] = 4; }
    for (int x = 2; x < 11; x++) frame[animationFrame * 13 + x] = 7;
  } else if (cameraState == WIFI_CONNECT) {
    frame[7 * 13 + 6] = 7;
    if (animationFrame >= 2) { frame[5 * 13 + 5] = 5; frame[5 * 13 + 7] = 5; }
    if (animationFrame >= 4) { frame[3 * 13 + 3] = 4; frame[3 * 13 + 9] = 4; frame[4 * 13 + 4] = 4; frame[4 * 13 + 8] = 4; }
    if (animationFrame >= 6) { frame[1 * 13 + 1] = 3; frame[1 * 13 + 11] = 3; frame[2 * 13 + 2] = 3; frame[2 * 13 + 10] = 3; }
  } else if (cameraState == COUNTDOWN || cameraState == GENERATING || cameraState == PRINTING) {
    for (int x = 0; x < 13; x++) frame[animationFrame * 13 + x] = x == animationFrame ? 7 : 1;
  } else if (cameraState == CAPTURE) {
    for (int i = 0; i < 104; i++) frame[i] = 7;
  } else if (cameraState == DONE || cameraState == WIFI_SUCCESS) {
    for (int i = 0; i < 4; i++) frame[(4 + i) * 13 + (2 + i)] = 7;
    for (int i = 0; i < 5; i++) frame[(6 - i) * 13 + (6 + i)] = 7;
  } else if (cameraState == ERROR_STATE || cameraState == WIFI_ERROR) {
    for (int i = 1; i < 7; i++) { frame[i * 13 + i + 2] = 7; frame[i * 13 + 10 - i] = 7; }
  }
  matrix.draw(frame);
}