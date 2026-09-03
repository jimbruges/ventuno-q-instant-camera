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
const promptInput = document.querySelector('#prompt');
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
let startedAt = 0;
let timer = 0;
let settingsDirty = false;
let pendingSettings = null;
let captureAfterSettingsSave = false;

ui.on_connect(() => { connection.textContent = 'BOARD ONLINE'; connection.classList.add('online'); ui.send_message('get_state'); });
ui.on_disconnect(() => { connection.textContent = 'DISCONNECTED'; connection.classList.remove('online'); shutter.disabled = true; });
ui.on_message('camera_state', render);
ui.on_message('settings_error', ({ message: error }) => {
  pendingSettings = null;
  captureAfterSettingsSave = false;
  settingsStatus.textContent = error;
  settingsStatus.className = 'error';
});
shutter.addEventListener('click', () => {
  if (settingsDirty) {
    captureAfterSettingsSave = true;
    settingsForm.requestSubmit();
  } else if (pendingSettings) {
    captureAfterSettingsSave = true;
  } else {
    ui.send_message('take_photo');
  }
});
settingsForm.addEventListener('input', () => { settingsDirty = true; settingsStatus.textContent = 'UNSAVED'; settingsStatus.className = ''; updateSettingOutputs(); });
settingsForm.addEventListener('submit', event => {
  event.preventDefault();
  settingsDirty = false;
  settingsStatus.textContent = 'SAVING';
  pendingSettings = {
    prompt: promptInput.value,
    resolution: Number(resolutionInput.value),
    steps: Number(stepsInput.value),
    guidance_scale: Number(guidanceInput.value),
    image_guidance_scale: Number(imageGuidanceInput.value),
    seed: randomSeedInput.checked ? null : Number(seedInput.value),
  };
  ui.send_message('set_settings', pendingSettings);
});
randomSeedInput.addEventListener('change', updateSeedControl);
referenceInput.addEventListener('change', () => {
  const [file] = referenceInput.files;
  if (!file) return;
  if (file.size > 9 * 1024 * 1024) {
    settingsStatus.textContent = 'REFERENCE IMAGE IS TOO LARGE';
    settingsStatus.className = 'error';
    referenceInput.value = '';
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
      ui.send_message('set_reference', { data_url: canvas.toDataURL('image/jpeg', 0.9) });
      referenceInput.value = '';
    });
    image.addEventListener('error', () => {
      settingsStatus.textContent = 'FAILED TO LOAD REFERENCE';
      settingsStatus.className = 'error';
      referenceInput.value = '';
    });
    image.src = reader.result;
  });
  reader.readAsDataURL(file);
});
removeReference.addEventListener('click', () => {
  settingsStatus.textContent = 'REMOVING REFERENCE';
  ui.send_message('set_reference', { data_url: '' });
});

function render(state) {
  statusText.textContent = state.status.toUpperCase();
  message.textContent = state.message;
  backend.textContent = state.backend === 'local' ? 'LOCAL AI' : state.backend.toUpperCase();
  shutter.disabled = state.busy;
  settingsForm.querySelector('fieldset')?.toggleAttribute('disabled', state.busy);
  applySettings.disabled = state.busy;
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
    const caption = document.createElement('figcaption'); caption.textContent = new Date(photo.created).toLocaleString();
    figure.append(image, caption); return figure;
  }));
  if (state.settings && !settingsDirty) {
    promptInput.value = state.settings.prompt;
    resolutionInput.value = state.settings.resolution;
    stepsInput.value = state.settings.steps;
    guidanceInput.value = state.settings.guidance_scale;
    imageGuidanceInput.value = state.settings.image_guidance_scale;
    randomSeedInput.checked = state.settings.seed === null;
    if (state.settings.seed !== null) seedInput.value = state.settings.seed;
    settingsStatus.textContent = 'APPLIED';
    settingsStatus.className = 'saved';
    updateSeedControl();
    updateSettingOutputs();
  }
  if (pendingSettings && settingsMatch(state.settings, pendingSettings)) {
    pendingSettings = null;
    if (captureAfterSettingsSave) {
      captureAfterSettingsSave = false;
      ui.send_message('take_photo');
    }
  }
  const hasReference = Boolean(state.npu_reference);
  referencePreview.style.display = hasReference ? 'block' : 'none';
  removeReference.style.display = hasReference ? 'block' : 'none';
  referenceName.textContent = hasReference ? 'REFERENCE READY' : 'NONE SELECTED';
  if (hasReference) referencePreview.src = state.npu_reference;
}

function updateElapsed() {
  elapsed.textContent = `${((Date.now() - startedAt) / 1000).toFixed(1)}s`;
}

function updateSettingOutputs() {
  document.querySelector('#steps-value').textContent = stepsInput.value;
  document.querySelector('#guidance-value').textContent = guidanceInput.value;
  document.querySelector('#image-guidance-value').textContent = imageGuidanceInput.value;
}

function updateSeedControl() {
  seedInput.disabled = randomSeedInput.checked;
  seedField.classList.toggle('inactive', randomSeedInput.checked);
}

function settingsMatch(actual, expected) {
  return actual
    && actual.prompt === expected.prompt
    && actual.resolution === expected.resolution
    && actual.steps === expected.steps
    && actual.guidance_scale === expected.guidance_scale
    && actual.image_guidance_scale === expected.image_guidance_scale
    && actual.seed === expected.seed;
}
