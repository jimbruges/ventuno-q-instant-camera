#include <Arduino_LED_Matrix.h>
#include <Arduino_Modulino.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;
ModulinoButtons buttons;

enum CameraState { READY, COUNTDOWN, CAPTURE, GENERATING, DONE, ERROR_STATE };
CameraState cameraState = READY;
bool previousPressed = false;
unsigned long lastFrameAt = 0;
uint8_t animationFrame = 0;

void drawState();
void setCameraStatus(String status);

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  Bridge.begin();
  Bridge.provide("camera_status", setCameraStatus);
  Modulino.begin(Wire1);
  buttons.begin();
  drawState();
}

void loop() {
  bool pressed = buttons.update() && buttons.isPressed(0) == HIGH;
  if (pressed && !previousPressed) {
    Bridge.notify("take_photo");
  }
  previousPressed = pressed;

  if ((cameraState == COUNTDOWN || cameraState == GENERATING) && millis() - lastFrameAt > 160) {
    lastFrameAt = millis();
    animationFrame = (animationFrame + 1) % 8;
    drawState();
  }
  delay(15);
}

void setCameraStatus(String status) {
  if (status == "ready") cameraState = READY;
  else if (status == "countdown") cameraState = COUNTDOWN;
  else if (status == "capture") cameraState = CAPTURE;
  else if (status == "generating") cameraState = GENERATING;
  else if (status == "done") cameraState = DONE;
  else cameraState = ERROR_STATE;
  animationFrame = 0;
  drawState();
}

void drawState() {
  uint8_t frame[104] = {0};
  if (cameraState == READY) {
    for (int x = 3; x < 10; x++) { frame[2 * 13 + x] = 3; frame[6 * 13 + x] = 3; }
    for (int y = 2; y < 7; y++) { frame[y * 13 + 3] = 3; frame[y * 13 + 9] = 3; }
    frame[3 * 13 + 6] = 5; frame[4 * 13 + 5] = 5; frame[4 * 13 + 6] = 7;
    frame[4 * 13 + 7] = 5; frame[5 * 13 + 6] = 5;
  } else if (cameraState == COUNTDOWN || cameraState == GENERATING) {
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