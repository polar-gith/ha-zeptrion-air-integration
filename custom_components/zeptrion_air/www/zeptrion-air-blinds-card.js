// Escape text before it is placed into innerHTML (element content or a
// quoted attribute value), so card config cannot inject markup.
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

class ZeptrionAirBlindsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }
  
  setConfig(config) {
    
    if (!config.entity) {
      throw new Error('You need to define a cover entity');
    }
    
    // Scene buttons call zeptrion_air.blind_recall_s1-s4 on the cover itself.
    // scene1_entity ... scene4_entity (button entities) are still honoured if set.
    this.config = { ...config };
    
    this.render();
  }
  
  set hass(hass) {
    this._hass = hass;
    this.updateCard();
  }
  
  render() {
    
    if (!this.config) {
      this.shadowRoot.innerHTML = '<div>No config set</div>';
      return;
    }
    
    this.shadowRoot.innerHTML = `
      <style>
        .card-header {
          font-weight: 500;
          color: var(--primary-text-color);
        }
        .controls-container {
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .button-row {
          display: flex;
          gap: 8px;
          justify-content: center;
        }
        .control-button {
          flex: 1;
          min-height: 44px;
          border: none;
          border-radius: 8px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s ease;
          font-size: 14px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 4px;
        }
        .primary-button {
          background: var(--primary-color);
          color: var(--text-primary-color);
        }
        .primary-button:hover {
          background: var(--primary-color);
          opacity: 0.8;
        }
        .secondary-button {
          background: var(--card-background-color);
          color: var(--primary-text-color);
          border: 1px solid var(--divider-color);
        }
        .secondary-button:hover {
          background: var(--secondary-background-color);
        }
        .tilt-button {
          background: var(--light-primary-color, #e3f2fd);
          color: var(--primary-color);
          border: 1px solid var(--primary-color);
        }
        .tilt-button:hover {
          background: var(--primary-color);
          color: var(--text-primary-color);
        }
      </style>
      
      <ha-card>
        <div class="card-header"><div class="name">${escapeHtml(this.config.name || 'Zeptrion Air Blinds')}</div></div>
        <div class="controls-container">
          <div class="button-row">
            <button class="control-button primary-button" id="up-btn">
              <ha-icon icon="mdi:arrow-up-bold"></ha-icon>
            </button>
            <button class="control-button tilt-button" id="tilt-open-btn">
              <ha-icon icon="mdi:arrow-up-thin"></ha-icon>
            </button>
            <button class="control-button secondary-button" id="stop-btn">
              <ha-icon icon="mdi:stop"></ha-icon>
            </button>
            <button class="control-button tilt-button" id="tilt-close-btn">
              <ha-icon icon="mdi:arrow-down-thin"></ha-icon>
            </button>
            <button class="control-button primary-button" id="down-btn">
              <ha-icon icon="mdi:arrow-down-bold"></ha-icon>
            </button>
          </div>
          
          <div class="button-row">
            <button class="control-button scene-button" id="scene1-btn"><ha-icon icon="mdi:numeric-1-box-outline"></ha-icon></button>
            <button class="control-button scene-button" id="scene2-btn"><ha-icon icon="mdi:numeric-2-box-outline"></ha-icon></button>
            <button class="control-button scene-button" id="scene3-btn"><ha-icon icon="mdi:numeric-3-box-outline"></ha-icon></button>
            <button class="control-button scene-button" id="scene4-btn"><ha-icon icon="mdi:numeric-4-box-outline"></ha-icon></button>
          </div>
        </div>
      </ha-card>
    `;

    this.setupEventListeners();
  }

  setupEventListeners() {
    // Standard cover controls
    this.shadowRoot.getElementById('up-btn').addEventListener('click', () => {
      this.callService('cover', 'open_cover');
    });

    this.shadowRoot.getElementById('down-btn').addEventListener('click', () => {
      this.callService('cover', 'close_cover');
    });

    this.shadowRoot.getElementById('stop-btn').addEventListener('click', () => {
      this.callService('cover', 'stop_cover');
    });

    // Custom step controls - call button entities
    this.shadowRoot.getElementById('tilt-open-btn').addEventListener('click', () => {
      this.callService('cover', 'open_cover_tilt');
    });

    this.shadowRoot.getElementById('tilt-close-btn').addEventListener('click', () => {
      this.callService('cover', 'close_cover_tilt');
    });

    // Scene recall buttons - call button entities
    this.shadowRoot.getElementById('scene1-btn').addEventListener('click', () => {
      this.recallScene(1);
    });

    this.shadowRoot.getElementById('scene2-btn').addEventListener('click', () => {
      this.recallScene(2);
    });

    this.shadowRoot.getElementById('scene3-btn').addEventListener('click', () => {
      this.recallScene(3);
    });

    this.shadowRoot.getElementById('scene4-btn').addEventListener('click', () => {
      this.recallScene(4);
    });
  }

  callService(domain, service, data = {}) {
    this._hass.callService(domain, service, {
      entity_id: this.config.entity,
      ...data
    });
  }

  recallScene(scene) {
    const button = this.config[`scene${scene}_entity`];
    if (button) {
      this.pressButton(button);
      return;
    }
    this.callService('zeptrion_air', `blind_recall_s${scene}`);
  }

  pressButton(entityId) {
    if (!entityId) {
      console.error('Button entity not configured');
      return;
    }
    
    // Check if entity exists
    if (!this._hass.states[entityId]) {
      console.error(`Button entity ${entityId} not found`);
      return;
    }
    console.log(`Pressing button: ${entityId}`);
    this._hass.callService('button', 'press', {
      entity_id: entityId
    });
  }

  updateCard() {
    if (!this._hass || !this.config.entity) return;
    // do nothing for now
  }
  
  getCardSize() {
    return 3;
  }
  
  static getConfigElement() {
    return document.createElement('zeptrion-air-blinds-card-editor');
  }
  
  static getStubConfig() {
    return {
      entity: 'cover.zeptrion_air_blinds',
      name: 'Zeptrion Air Blinds'
    };
  }
}

// Configuration editor
class ZeptrionAirBlindsCardEditor extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.render();
  }

  render() {
    this.innerHTML = `
      <div style="padding: 16px;">
        <label style="display: block; margin-bottom: 8px;">Cover Entity:</label>
        <input type="text" id="entity" value="${escapeHtml(this.config.entity || '')}" 
               style="width: 100%; margin-bottom: 16px; padding: 8px;" />
        
        <label style="display: block; margin-bottom: 8px;">Name:</label>
        <input type="text" id="name" value="${escapeHtml(this.config.name || '')}" 
               style="width: 100%; margin-bottom: 16px; padding: 8px;" />
        
        <details style="margin-bottom: 16px;">
          <summary style="cursor: pointer; font-weight: bold;">Button Entity Configuration</summary>
          <div style="margin-top: 8px;">
            <p style="font-size: 0.9em; color: #666; margin-bottom: 12px;">
              Button entities are auto-detected based on cover entity name. Override if needed:
            </p>
            
            <label style="display: block; margin-bottom: 4px;">Scene 1 Button:</label>
            <input type="text" id="scene1_entity" value="${escapeHtml(this.config.scene1_entity || '')}" 
                   style="width: 100%; margin-bottom: 8px; padding: 6px;" />
            
            <label style="display: block; margin-bottom: 4px;">Scene 2 Button:</label>
            <input type="text" id="scene2_entity" value="${escapeHtml(this.config.scene2_entity || '')}" 
                   style="width: 100%; margin-bottom: 8px; padding: 6px;" />
            
            <label style="display: block; margin-bottom: 4px;">Scene 3 Button:</label>
            <input type="text" id="scene3_entity" value="${escapeHtml(this.config.scene3_entity || '')}" 
                   style="width: 100%; margin-bottom: 8px; padding: 6px;" />
            
            <label style="display: block; margin-bottom: 4px;">Scene 4 Button:</label>
            <input type="text" id="scene4_entity" value="${escapeHtml(this.config.scene4_entity || '')}" 
                   style="width: 100%; margin-bottom: 8px; padding: 6px;" />
          </div>
        </details>
      </div>
    `;

    this.addEventListener('input', this.configChanged);
  }

  configChanged(ev) {
    const config = {
      ...this.config,
      [ev.target.id]: ev.target.value
    };
    
    const event = new CustomEvent('config-changed', {
      detail: { config },
      bubbles: true,
      composed: true
    });
    this.dispatchEvent(event);
  }
}

// The card can be loaded twice (extra_module_url and Lovelace resource);
// guard against a second define, which would throw.
if (!customElements.get('zeptrion-air-blinds-card')) {
  customElements.define('zeptrion-air-blinds-card', ZeptrionAirBlindsCard);
}
if (!customElements.get('zeptrion-air-blinds-card-editor')) {
  customElements.define('zeptrion-air-blinds-card-editor', ZeptrionAirBlindsCardEditor);
}

// Register the card (once, also if the file is loaded twice)
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === 'zeptrion-air-blinds-card')) {
  window.customCards.push({
    type: 'zeptrion-air-blinds-card',
    name: 'Zeptrion Air Blinds Card',
    description: 'Blind controls with scenes S1–S4. For new dashboards use a tile card with the feature "Zeptrion scenes S1–S4".'
  });
}

// ---------------------------------------------------------------------------
// Tile card feature "Zeptrion scenes S1–S4" for Zeptrion Air blinds and lights.
//
//   type: tile
//   entity: cover.living_room_blind
//   features:
//     - type: custom:zeptrion-air-scenes
//
// Blinds call zeptrion_air.blind_recall_s1-s4, lights zeptrion_air.light_recall_scene,
// always on the tile's own entity (no guessed entity IDs).
// ---------------------------------------------------------------------------

const ZEPTRION_SCENES = [1, 2, 3, 4];

function zeptrionSceneEntityId(stateObjOrHass, context) {
  // Home Assistant passes (hass, context) since 2025.x, (stateObj) before.
  if (context && context.entity_id) return context.entity_id;
  if (stateObjOrHass && stateObjOrHass.entity_id) return stateObjOrHass.entity_id;
  return undefined;
}

function zeptrionScenesSupported(stateObjOrHass, context) {
  const entityId = zeptrionSceneEntityId(stateObjOrHass, context);
  if (!entityId) return false;
  const domain = entityId.split('.')[0];
  if (domain !== 'cover' && domain !== 'light') return false;
  const entities = stateObjOrHass && stateObjOrHass.entities;
  if (entities && entities[entityId]) {
    return entities[entityId].platform === 'zeptrion_air';
  }
  return true; // older frontends: no entity registry here
}

class ZeptrionAirScenesFeature extends HTMLElement {
  static getStubConfig() {
    return { type: 'custom:zeptrion-air-scenes' };
  }

  setConfig(config) {
    if (!config) throw new Error('Invalid configuration');
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  // Since Home Assistant 2025.x features get a context with the entity ID.
  set context(context) {
    this._context = context;
    this._render();
  }

  // Older Home Assistant versions pass the state object instead.
  set stateObj(stateObj) {
    this._stateObj = stateObj;
    this._render();
  }

  get _entityId() {
    return (this._context && this._context.entity_id) || (this._stateObj && this._stateObj.entity_id);
  }

  _render() {
    const entityId = this._entityId;
    if (!this._config || !entityId) return;
    if (this._renderedFor === entityId) return;
    this._renderedFor = entityId;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .row { display: flex; gap: 8px; height: var(--feature-height, 42px); }
        button {
          flex: 1 1 0;
          min-width: 0;
          border: none;
          border-radius: var(--feature-border-radius, 12px);
          background: var(--tile-color, var(--primary-color));
          background: color-mix(in srgb, var(--tile-color, var(--primary-color)) 20%, transparent);
          color: var(--primary-text-color);
          font: inherit;
          font-weight: 500;
          cursor: pointer;
        }
        button:hover { background: color-mix(in srgb, var(--tile-color, var(--primary-color)) 35%, transparent); }
        button:disabled { opacity: 0.5; cursor: default; }
      </style>
      <div class="row">
        ${ZEPTRION_SCENES.map(
          (n) => `<button type="button" data-scene="${n}" title="Scene S${n}" aria-label="Scene S${n}">S${n}</button>`
        ).join('')}
      </div>`;
    this.shadowRoot.querySelectorAll('button').forEach((button) => {
      button.addEventListener('click', (ev) => {
        ev.stopPropagation();
        this._recall(Number(button.dataset.scene));
      });
    });
    this._update();
  }

  _update() {
    if (!this.shadowRoot || !this._hass) return;
    const state = this._hass.states[this._entityId];
    const unavailable = !state || state.state === 'unavailable';
    this.shadowRoot.querySelectorAll('button').forEach((button) => {
      button.disabled = unavailable;
    });
  }

  _recall(scene) {
    const entityId = this._entityId;
    if (!this._hass || !entityId) return;
    if (entityId.startsWith('cover.')) {
      this._hass.callService('zeptrion_air', `blind_recall_s${scene}`, { entity_id: entityId });
    } else {
      this._hass.callService('zeptrion_air', 'light_recall_scene', { entity_id: entityId, scene });
    }
  }
}

if (!customElements.get('zeptrion-air-scenes')) {
  customElements.define('zeptrion-air-scenes', ZeptrionAirScenesFeature);
  window.customCardFeatures = window.customCardFeatures || [];
  window.customCardFeatures.push({
    type: 'zeptrion-air-scenes',
    name: 'Zeptrion scenes S1–S4',
    supported: zeptrionScenesSupported,
    configurable: false,
  });
}
