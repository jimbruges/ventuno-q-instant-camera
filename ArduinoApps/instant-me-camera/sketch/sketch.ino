#include <Adafruit_Thermal.h>
#include <Arduino_LED_Matrix.h>
#include <Arduino_Modulino.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;
ModulinoButtons buttons;
ModulinoPixels pixels;
Adafruit_Thermal printer(&Serial1);

enum CameraState { READY, COUNTDOWN, CAPTURE, GENERATING, PRINTING, DONE, ERROR_STATE };
CameraState cameraState = READY;
bool previousPressed[3] = {false, false, false};
bool optionsAvailable[3] = {false, false, false};
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
void setOptions(bool normalAvailable, bool cloudAvailable, bool localAvailable);
void setCameraStatus(String status);
bool beginPrint(uint16_t rows);
bool appendPrintRows(std::vector<int> packedRows);
bool endPrint(uint8_t feedLines);
bool cancelPrint();
bool configurePrinter(uint8_t heatDots, uint8_t heatTime, uint8_t heatInterval, uint8_t density, uint8_t breakTime);
bool testPrint();

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  Serial1.begin(19200);
  printer.begin();
  Bridge.begin();
  Bridge.provide("camera_status", setCameraStatus);
  Bridge.provide("print_begin", beginPrint);
  Bridge.provide("print_rows", appendPrintRows);
  Bridge.provide("print_end", endPrint);
  Bridge.provide("print_cancel", cancelPrint);
  Bridge.provide("print_configure", configurePrinter);
  Bridge.provide("print_test", testPrint);
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
  buttons.update();
  const char *modes[3] = {"normal", "cloud", "local"};
  for (int index = 0; index < 3; index++) {
    bool pressed = buttons.isPressed(index) == HIGH;
    if (pressed && !previousPressed[index] && optionsAvailable[index] && cameraState == READY) {
      Bridge.notify("take_photo", modes[index]);
    }
    previousPressed[index] = pressed;
  }

  if ((cameraState == COUNTDOWN || cameraState == GENERATING || cameraState == PRINTING) && millis() - lastFrameAt > 160) {
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

void setOptions(bool normalAvailable, bool cloudAvailable, bool localAvailable) {
  optionsAvailable[0] = normalAvailable;
  optionsAvailable[1] = cloudAvailable;
  optionsAvailable[2] = localAvailable;
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
  else cameraState = ERROR_STATE;
  animationFrame = 0;
  if (cameraState == CAPTURE) {
    for (int index = 0; index < 8; index++) pixels.set(index, WHITE, 80);
    pixels.show();
    captureFlashUntil = millis() + 260;
  }
  drawState();
  updateControls();
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
  printer.setPrintDensity(density, breakTime);
  return true;
}

bool testPrint() {
  if (printJobActive) return false;
  cameraState = PRINTING;
  drawState();
  updateControls();
  printer.justify('C');
  printer.boldOn();
  printer.println("INSTANT ME");
  printer.boldOff();
  printer.println("Printer test OK");
  printer.feed(3);
  cameraState = DONE;
  drawState();
  updateControls();
  return true;
}

void updateControls() {
  if (cameraState == READY) {
    buttons.setLeds(optionsAvailable[0], optionsAvailable[1], optionsAvailable[2]);
    updatePixels();
    return;
  }
  bool flashOn = animationFrame % 2 == 0;
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
  if (cameraState == READY) {
    if (optionsAvailable[0]) { pixels.set(0, 35, 180, 95, 22); pixels.set(1, 35, 180, 95, 22); }
    if (optionsAvailable[1]) { pixels.set(3, 45, 125, 220, 22); pixels.set(4, 45, 125, 220, 22); }
    if (optionsAvailable[2]) { pixels.set(6, 230, 155, 35, 22); pixels.set(7, 230, 155, 35, 22); }
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
  } else if (cameraState == COUNTDOWN || cameraState == GENERATING || cameraState == PRINTING) {
    for (int x = 0; x < 13; x++) frame[animationFrame * 13 + x] = x == animationFrame ? 7 : 1;
  } else if (cameraState == CAPTURE) {
    for (int i = 0; i < 104; i++) frame[i] = 7;
  } else if (cameraState == DONE) {
    for (int i = 0; i < 4; i++) frame[(4 + i) * 13 + (2 + i)] = 7;
    for (int i = 0; i < 5; i++) frame[(6 - i) * 13 + (6 + i)] = 7;
  } else {
    for (int i = 1; i < 7; i++) { frame[i * 13 + i + 2] = 7; frame[i * 13 + 10 - i] = 7; }
  }
  matrix.draw(frame);
}