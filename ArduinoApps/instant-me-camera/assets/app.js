const ui = new WebUI();
const shutter = document.querySelector('#shutter');
const latest = document.querySelector('#latest');
const empty = document.querySelector('#empty');
const statusText = document.querySelector('#status');
const message = document.querySelector('#message');
const backend = document.querySelector('#backend');
const gallery = document.querySelector('#gallery');
const count = document.querySelector('#count');
const connection = document.querySelector('#connection');
const dot = document.querySelector('#status-dot');
const progress = document.querySelector('#progress');
const elapsed = document.querySelector('#elapsed');
const settingsForm = document.querySelector('#settings-form');
const settingsStatus = document.querySelector('#settings-status');
const settingsTitle = document.querySelector('#settings-title');
const profileModeInput = document.querySelector('#profile-mode');
const promptInput = document.querySelector('#prompt');
const cloudPromptInput = document.querySelector('#cloud-prompt');
const openrouterModelInput = document.querySelector('#openrouter-model');
const openrouterApiKeyInput = document.querySelector('#openrouter-api-key');
const openrouterKeyStatus = document.querySelector('#openrouter-key-status');
const removeOpenrouterKey = document.querySelector('#remove-openrouter-key');
const cloudReferenceInput = document.querySelector('#cloud-reference-input');
const cloudReferencePreview = document.querySelector('#cloud-reference-preview');
const cloudReferenceName = document.querySelector('#cloud-reference-name');
const removeCloudReference = document.querySelector('#remove-cloud-reference');
const npuModelInput = document.querySelector('#npu-model');
const modelDescription = document.querySelector('#model-description');
const resolutionInput = document.querySelector('#resolution');
const stepsInput = document.querySelector('#steps');
const guidanceInput = document.querySelector('#guidance');
const imageGuidanceInput = document.querySelector('#image-guidance');
const referenceInput = document.querySelector('#reference-input');
const referencePreview = document.querySelector('#reference-preview');
const referenceName = document.querySelector('#reference-name');
const removeReference = document.querySelector('#remove-reference');
const randomSeedInput = document.querySelector('#random-seed');
const seedInput = document.querySelector('#seed');
const seedField = document.querySelector('#seed-field');
const applySettings = document.querySelector('#apply-settings');
const brightnessInput = document.querySelector('#camera-brightness');
const contrastInput = document.querySelector('#camera-contrast');
const printerEnabledInput = document.querySelector('#printer-enabled');
const printerDitherInput = document.querySelector('#printer-dither');
const printerThresholdInput = document.querySelector('#printer-threshold');
const printerFeedInput = document.querySelector('#printer-feed');
const printerHeatDotsInput = document.querySelector('#printer-heat-dots');
const printerHeatTimeInput = document.querySelector('#printer-heat-time');
const printerHeatIntervalInput = document.querySelector('#printer-heat-interval');
const printerDensityInput = document.querySelector('#printer-density');
const printerBreakTimeInput = document.querySelector('#printer-break-time');
const testPrint = document.querySelector('#test-print');
const capture = document.querySelector('#capture');
const profileButtons = [...document.querySelectorAll('.mode-button')];
const modePanels = [...document.querySelectorAll('.mode-panel')];
let startedAt = 0;
let timer = 0;
let settingsDirty = false;
let pendingSettings = null;
let captureAfterSettingsSave = false;
let selectedProfileId = 'a_short';
let selectedMode = 'normal';
let currentAvailability = {};
let buttonProfiles = {};
let modelDefaults = {};
let profileReferences = {};
let selectedNpuModel = 'standard';
let removeSavedOpenrouterKey = false;

ui.on_connect(() => { connection.textContent = 'BOARD ONLINE'; connection.classList.add('online'); ui.send_message('get_state'); });
ui.on_disconnect(() => { connection.textContent = 'DISCONNECTED'; connection.classList.remove('online'); shutter.disabled = true; capture.disabled = true; profileButtons.forEach(button => { button.disabled = true; }); });
ui.on_message('camera_state', render);
ui.on_message('settings_error', ({ message: error }) => {
  pendingSettings = null;
  captureAfterSettingsSave = false;
  settingsDirty = true;
  settingsStatus.textContent = error;
  settingsStatus.className = 'error';
});
function requestSelectedCapture() {
  if (settingsDirty) {
    captureAfterSettingsSave = true;
    settingsForm.requestSubmit();
  } else if (pendingSettings) {
    captureAfterSettingsSave = true;
  } else {
    ui.send_message('take_photo', { profile_id: selectedProfileId });
  }
}
shutter.addEventListener('click', requestSelectedCapture);
capture.addEventListener('click', requestSelectedCapture);
profileButtons.forEach(button => button.addEventListener('click', () => selectProfile(button.dataset.profileId)));
testPrint.addEventListener('click', () => ui.send_message('test_print'));
settingsForm.addEventListener('input', () => { settingsDirty = true; settingsStatus.textContent = 'UNSAVED'; settingsStatus.className = ''; updateSettingOutputs(); });
settingsForm.addEventListener('submit', event => {
  event.preventDefault();
  settingsDirty = false;
  settingsStatus.textContent = 'SAVING';
  pendingSettings = {
    profile_id: selectedProfileId,
    mode: selectedMode,
    npu_model: selectedNpuModel,
    prompt: promptInput.value,
    cloud_prompt: cloudPromptInput.value,
    openrouter_model: openrouterModelInput.value,
    openrouter_api_key: openrouterApiKeyInput.value,
    remove_openrouter_api_key: removeSavedOpenrouterKey,
    resolution: Number(resolutionInput.value),
    steps: Number(stepsInput.value),
    guidance_scale: Number(guidanceInput.value),
    image_guidance_scale: Number(imageGuidanceInput.value),
    seed: randomSeedInput.checked ? null : Number(seedInput.value),
    camera_brightness: Number(brightnessInput.value),
    camera_contrast: Number(contrastInput.value),
    printer_enabled: printerEnabledInput.checked,
    printer_threshold: Number(printerThresholdInput.value),
    printer_dither: printerDitherInput.checked,
    printer_feed_lines: Number(printerFeedInput.value),
    printer_heat_dots: Number(printerHeatDotsInput.value),
    printer_heat_time: Number(printerHeatTimeInput.value),
    printer_heat_interval: Number(printerHeatIntervalInput.value),
    printer_density: Number(printerDensityInput.value),
    printer_break_time: Number(printerBreakTimeInput.value),
  };
  ui.send_message('set_settings', pendingSettings);
  openrouterApiKeyInput.value = '';
});
removeOpenrouterKey.addEventListener('click', () => {
  openrouterApiKeyInput.value = '';
  removeSavedOpenrouterKey = true;
  settingsDirty = true;
  settingsStatus.textContent = 'KEY WILL BE REMOVED';
  settingsStatus.className = '';
});
openrouterApiKeyInput.addEventListener('input', () => {
  if (openrouterApiKeyInput.value) removeSavedOpenrouterKey = false;
});
npuModelInput.addEventListener('change', () => {
  selectedNpuModel = npuModelInput.value;
  loadLocalProfile(modelDefaults[selectedNpuModel]);
  settingsDirty = true;
  settingsStatus.textContent = 'UNSAVED';
  settingsStatus.className = '';
});
profileModeInput.addEventListener('change', () => {
  selectedMode = profileModeInput.value;
  showModePanel(selectedMode);
  updateSelectedProfileButton();
});
randomSeedInput.addEventListener('change', updateSeedControl);
function bindReferenceControl(input, remove) {
input.addEventListener('change', () => {
  const [file] = input.files;
  if (!file) return;
  const profileId = selectedProfileId;
  if (file.size > 9 * 1024 * 1024) {
    settingsStatus.textContent = 'REFERENCE IMAGE IS TOO LARGE';
    settingsStatus.className = 'error';
    input.value = '';
    return;
  }
  const reader = new FileReader();
  reader.addEventListener('load', () => {
    const image = new Image();
    image.addEventListener('load', () => {
      const scale = Math.min(1, 1024 / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(image.naturalWidth * scale);
      canvas.height = Math.round(image.naturalHeight * scale);
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
      settingsStatus.textContent = 'UPLOADING REFERENCE';
      ui.send_message('set_reference', { profile_id: profileId, data_url: canvas.toDataURL('image/jpeg', 0.9) });
      input.value = '';
    });
    image.addEventListener('error', () => {
      settingsStatus.textContent = 'FAILED TO LOAD REFERENCE';
      settingsStatus.className = 'error';
      input.value = '';
    });
    image.src = reader.result;
  });
  reader.readAsDataURL(file);
});
remove.addEventListener('click', () => {
  settingsStatus.textContent = 'REMOVING REFERENCE';
  ui.send_message('set_reference', { profile_id: selectedProfileId, data_url: '' });
});
}
bindReferenceControl(referenceInput, removeReference);
bindReferenceControl(cloudReferenceInput, removeCloudReference);

function render(state) {
  statusText.textContent = state.status.toUpperCase();
  message.textContent = state.message;
  backend.textContent = state.active_gesture
    ? `${formatProfileName(state.active_gesture)} · ${state.active_mode.toUpperCase()}`
    : 'SIX PROFILES';
  shutter.disabled = state.busy;
  const availability = state.availability || {};
  currentAvailability = availability;
  if (state.settings) {
    modelDefaults = structuredClone(state.settings.npu_model_defaults || {});
    if (!settingsDirty) buttonProfiles = structuredClone(state.settings.button_profiles || {});
  }
  profileReferences = state.profile_references || {};
  profileButtons.forEach(button => {
    const profileId = button.dataset.profileId;
    const available = Boolean(availability[profileId]);
    const profile = buttonProfiles[profileId];
    button.disabled = state.busy;
    button.classList.toggle('available', available);
    button.classList.toggle('selected', selectedProfileId === profileId);
    button.classList.toggle('active', state.active_gesture === profileId);
    if (profile) button.querySelector('[data-profile-mode]').textContent = profile.mode.toUpperCase();
  });
  modePanels.forEach(panel => { panel.disabled = state.busy || panel.hidden; });
  [...settingsForm.querySelectorAll('.common-panel')].forEach(panel => { panel.disabled = state.busy; });
  applySettings.disabled = state.busy;
  capture.disabled = state.busy || !availability[selectedProfileId];
  progress.classList.toggle('active', state.busy);
  if (state.busy && !startedAt) {
    startedAt = Date.now();
    timer = setInterval(updateElapsed, 100);
  } else if (!state.busy && startedAt) {
    clearInterval(timer);
    startedAt = 0;
    elapsed.textContent = '';
  }
  dot.style.background = state.status === 'error' ? '#df3e2c' : state.busy ? '#d9a329' : '#2b9b68';
  const photos = state.photos || [];
  count.textContent = `${photos.length} EXPOSURE${photos.length === 1 ? '' : 'S'}`;
  if (photos.length) {
    latest.onload = () => latest.parentElement.style.setProperty('--frame-ratio', `${latest.naturalWidth} / ${latest.naturalHeight}`);
    latest.src = `${photos[0].image}?v=${Date.now()}`;
    latest.style.display = 'block'; empty.style.display = 'none';
  }
  gallery.replaceChildren(...photos.map((photo, index) => {
    const figure = document.createElement('figure'); figure.className = 'photo'; figure.style.setProperty('--tilt', `${[-1.2,.7,-.5][index % 3]}deg`);
    const image = document.createElement('img'); image.src = photo.image; image.alt = 'Generated instant photograph'; image.loading = 'lazy';
    const caption = document.createElement('figcaption'); caption.textContent = `${photo.mode.toUpperCase()} · ${new Date(photo.created).toLocaleString()}`;
    figure.append(image, caption);
    if (photo.print) {
      const reprint = document.createElement('button'); reprint.type = 'button'; reprint.className = 'reprint'; reprint.textContent = 'REPRINT';
      reprint.disabled = state.busy; reprint.addEventListener('click', () => ui.send_message('reprint', { id: photo.id })); figure.append(reprint);
    }
    return figure;
  }));
  if (state.settings && !settingsDirty) {
    loadSelectedProfile();
    openrouterApiKeyInput.value = '';
    removeSavedOpenrouterKey = false;
    brightnessInput.value = state.settings.camera_brightness;
    contrastInput.value = state.settings.camera_contrast;
    printerEnabledInput.checked = state.settings.printer_enabled;
    printerThresholdInput.value = state.settings.printer_threshold;
    printerDitherInput.checked = state.settings.printer_dither !== false;
    printerFeedInput.value = state.settings.printer_feed_lines;
    printerHeatDotsInput.value = state.settings.printer_heat_dots;
    printerHeatTimeInput.value = state.settings.printer_heat_time;
    printerHeatIntervalInput.value = state.settings.printer_heat_interval;
    printerDensityInput.value = state.settings.printer_density;
    printerBreakTimeInput.value = state.settings.printer_break_time;
    settingsStatus.textContent = 'APPLIED';
    settingsStatus.className = 'saved';
    updateSeedControl();
    updateSettingOutputs();
  }
  const keySource = state.openrouter_key_source;
  openrouterKeyStatus.textContent = keySource === 'saved' ? 'SAVED ON DEVICE' : keySource === 'environment' ? 'APP CONFIGURATION' : 'NOT CONFIGURED';
  openrouterApiKeyInput.placeholder = state.openrouter_key_ready ? 'LEAVE BLANK TO KEEP CURRENT KEY' : 'PASTE OPENROUTER KEY';
  removeOpenrouterKey.disabled = state.busy || keySource !== 'saved';
  if (pendingSettings && settingsMatch(state.settings, pendingSettings)) {
    pendingSettings = null;
    if (captureAfterSettingsSave) {
      captureAfterSettingsSave = false;
      ui.send_message('take_photo', { profile_id: selectedProfileId });
    }
  }
  renderSelectedReference();
}

function selectProfile(profileId, scroll = true) {
  if (settingsDirty) {
    settingsStatus.textContent = 'APPLY SETTINGS BEFORE SWITCHING PROFILE';
    settingsStatus.className = 'error';
    return;
  }
  selectedProfileId = profileId;
  loadSelectedProfile();
  profileButtons.forEach(button => button.classList.toggle('selected', button.dataset.profileId === profileId));
  capture.disabled = !currentAvailability[profileId];
  if (scroll) document.querySelector('.settings').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function showModePanel(mode) {
  modePanels.forEach(panel => {
    panel.hidden = panel.dataset.settingsMode !== mode;
    panel.disabled = panel.hidden;
  });
}

function formatProfileName(profileId) {
  const [button, press] = profileId.split('_');
  return `${button.toUpperCase()} ${press.toUpperCase()} PRESS`;
}

function updateSelectedProfileButton() {
  const button = profileButtons.find(item => item.dataset.profileId === selectedProfileId);
  if (button) button.querySelector('[data-profile-mode]').textContent = selectedMode.toUpperCase();
}

function loadSelectedProfile() {
  const profile = buttonProfiles[selectedProfileId];
  if (!profile) return;
  selectedMode = profile.mode;
  selectedNpuModel = profile.npu_model;
  settingsTitle.textContent = formatProfileName(selectedProfileId);
  profileModeInput.value = selectedMode;
  npuModelInput.value = selectedNpuModel;
  loadLocalProfile(profile);
  cloudPromptInput.value = profile.cloud_prompt;
  openrouterModelInput.value = profile.openrouter_model;
  showModePanel(selectedMode);
  updateSelectedProfileButton();
  renderSelectedReference();
}

function renderSelectedReference() {
  const referenceUrl = profileReferences[selectedProfileId];
  const hasReference = Boolean(referenceUrl);
  referencePreview.style.display = hasReference ? 'block' : 'none';
  removeReference.style.display = hasReference ? 'block' : 'none';
  referenceName.textContent = hasReference ? 'REFERENCE READY' : 'NONE SELECTED';
  cloudReferencePreview.style.display = hasReference ? 'block' : 'none';
  removeCloudReference.style.display = hasReference ? 'block' : 'none';
  cloudReferenceName.textContent = hasReference ? 'CONTEXT READY' : 'NONE SELECTED';
  if (hasReference) {
    referencePreview.src = referenceUrl;
    cloudReferencePreview.src = referenceUrl;
  }
}

function updateElapsed() {
  elapsed.textContent = `${((Date.now() - startedAt) / 1000).toFixed(1)}s`;
}

function updateSettingOutputs() {
  document.querySelector('#steps-value').textContent = stepsInput.value;
  document.querySelector('#guidance-value').textContent = guidanceInput.value;
  document.querySelector('#image-guidance-value').textContent = imageGuidanceInput.value;
  document.querySelector('#brightness-value').textContent = brightnessInput.value;
  document.querySelector('#contrast-value').textContent = contrastInput.value;
  document.querySelector('#threshold-value').textContent = printerThresholdInput.value;
}

function updateSeedControl() {
  seedInput.disabled = randomSeedInput.checked;
  seedField.classList.toggle('inactive', randomSeedInput.checked);
}

function readLocalProfile() {
  return {
    prompt: promptInput.value,
    resolution: Number(resolutionInput.value),
    steps: Number(stepsInput.value),
    guidance_scale: Number(guidanceInput.value),
    image_guidance_scale: Number(imageGuidanceInput.value),
    seed: randomSeedInput.checked ? null : Number(seedInput.value),
  };
}

function loadLocalProfile(profile) {
  if (!profile) return;
  promptInput.value = profile.prompt;
  resolutionInput.value = profile.resolution;
  stepsInput.value = profile.steps;
  guidanceInput.value = profile.guidance_scale;
  imageGuidanceInput.value = profile.image_guidance_scale;
  randomSeedInput.checked = profile.seed === null;
  if (profile.seed !== null) seedInput.value = profile.seed;
  modelDescription.textContent = selectedNpuModel === 'hyper'
    ? 'DISTILLED 4-STEP EDITOR · FIRST SWITCH LOADS MODEL'
    : 'FULL 20-STEP EDITOR · BEST COMPOSITION';
  updateSeedControl();
  updateSettingOutputs();
}

function settingsMatch(actual, expected) {
  const profile = actual && actual.button_profiles && actual.button_profiles[expected.profile_id];
  return actual
    && profile
    && profile.mode === expected.mode
    && profile.npu_model === expected.npu_model
    && profile.prompt === expected.prompt
    && profile.cloud_prompt === expected.cloud_prompt
    && profile.openrouter_model === expected.openrouter_model
    && profile.resolution === expected.resolution
    && profile.steps === expected.steps
    && profile.guidance_scale === expected.guidance_scale
    && profile.image_guidance_scale === expected.image_guidance_scale
    && profile.seed === expected.seed
    && actual.camera_brightness === expected.camera_brightness
    && actual.camera_contrast === expected.camera_contrast
    && actual.printer_enabled === expected.printer_enabled
    && actual.printer_threshold === expected.printer_threshold
    && (actual.printer_dither === undefined || actual.printer_dither === expected.printer_dither)
    && actual.printer_feed_lines === expected.printer_feed_lines
    && actual.printer_heat_dots === expected.printer_heat_dots
    && actual.printer_heat_time === expected.printer_heat_time
    && actual.printer_heat_interval === expected.printer_heat_interval
    && actual.printer_density === expected.printer_density
    && actual.printer_break_time === expected.printer_break_time;
}

showModePanel('normal');
