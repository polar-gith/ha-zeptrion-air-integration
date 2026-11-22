// Tests for the dashboard parts in zeptrion-air-blinds-card.js (no browser needed):
// tile card feature "Zeptrion scenes S1–S4", dashboard strategy, blinds card.
// Run: node --test tests/js/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const SOURCE = readFileSync(
  new URL('../../custom_components/zeptrion_air/www/zeptrion-air-blinds-card.js', import.meta.url),
  'utf8',
);

// Minimal DOM: enough for setConfig/render of the card and the feature.
class FakeElement {
  constructor() {
    this.listeners = {};
    this.children = [];
    this.dataset = {};
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  click() {
    (this.listeners.click || []).forEach((fn) => fn({ stopPropagation() {} }));
  }
  dispatchEvent() {}
}

class FakeShadowRoot {
  constructor() {
    this._html = '';
    this._buttons = new Map();
  }
  set innerHTML(html) {
    this._html = html;
    this._buttons = new Map();
    for (const [, attrs] of html.matchAll(/<button\b([^>]*)>/g)) {
      const button = new FakeElement();
      const id = (attrs.match(/\bid="([^"]+)"/) || [])[1];
      const scene = (attrs.match(/data-scene="(\d)"/) || [])[1];
      if (scene) button.dataset.scene = scene;
      this._buttons.set(id || `scene-${scene}`, button);
    }
  }
  get innerHTML() {
    return this._html;
  }
  getElementById(id) {
    return this._buttons.get(id) || new FakeElement();
  }
  querySelectorAll(selector) {
    return selector === 'button' ? [...this._buttons.values()] : [];
  }
}

class FakeHTMLElement extends FakeElement {
  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }
}

// Objects from the vm context have other prototypes: compare plain copies.
const plain = (value) => JSON.parse(JSON.stringify(value));

// The file is loaded as an ES module (own scope per load).
const asModule = (code) => `(function () {\n${code}\n})();`;

function load() {
  const registry = new Map();
  const window = {};
  const context = {
    HTMLElement: FakeHTMLElement,
    customElements: {
      get: (name) => registry.get(name),
      define: (name, cls) => {
        if (registry.has(name)) throw new Error(`${name} defined twice`);
        registry.set(name, cls);
      },
    },
    document: { createElement: () => new FakeHTMLElement() },
    window,
    console,
  };
  vm.createContext(context);
  vm.runInContext(asModule(SOURCE), context);
  return { registry, window, context };
}

function fakeHass() {
  const calls = [];
  const states = {
    'cover.blind': { entity_id: 'cover.blind', state: 'open', attributes: { supported_features: 191, friendly_name: 'Blind' } },
    'light.dimmer': { entity_id: 'light.dimmer', state: 'off', attributes: { supported_color_modes: ['brightness'], friendly_name: 'Dimmer' } },
    'light.switch': { entity_id: 'light.switch', state: 'off', attributes: { supported_color_modes: ['onoff'], friendly_name: 'Switch' } },
    'light.led': { entity_id: 'light.led', state: 'on', attributes: { supported_color_modes: ['rgb'] } },
    'light.other': { entity_id: 'light.other', state: 'on', attributes: {} },
    'cover.attic': { entity_id: 'cover.attic', state: 'open', attributes: { supported_features: 3, friendly_name: 'Attic' } },
  };
  const entities = {
    'cover.blind': { entity_id: 'cover.blind', platform: 'zeptrion_air', device_id: 'd1' },
    'light.dimmer': { entity_id: 'light.dimmer', platform: 'zeptrion_air', device_id: 'd1' },
    'light.switch': { entity_id: 'light.switch', platform: 'zeptrion_air', device_id: 'd2', area_id: 'cellar' },
    'light.led': { entity_id: 'light.led', platform: 'zeptrion_air', device_id: 'd1', translation_key: 'smartfront_led' },
    'light.other': { entity_id: 'light.other', platform: 'hue', device_id: 'd3' },
    'cover.attic': { entity_id: 'cover.attic', platform: 'zeptrion_air', device_id: 'd4' },
  };
  return {
    calls,
    states,
    entities,
    devices: { d1: { area_id: 'living' }, d2: { area_id: 'living' }, d4: {} },
    areas: {
      living: { area_id: 'living', name: 'Living', floor_id: 'eg', icon: 'mdi:sofa' },
      cellar: { area_id: 'cellar', name: 'Cellar', floor_id: 'ug' },
    },
    floors: { eg: { floor_id: 'eg', name: 'EG', level: 0 }, ug: { floor_id: 'ug', name: 'UG', level: -1 } },
    callService: (domain, service, data) => calls.push([domain, service, data]),
  };
}

test('defines the card, the feature and the strategy once, also when loaded twice', () => {
  const { registry, window, context } = load();
  assert.ok(registry.get('zeptrion-air-blinds-card'));
  assert.ok(registry.get('zeptrion-air-scenes'));
  assert.ok(registry.get('ll-strategy-dashboard-zeptrion-air'));
  vm.runInContext(asModule(SOURCE), context); // second load must not throw
  assert.equal(window.customCards.filter((c) => c.type === 'zeptrion-air-blinds-card').length, 1);
  assert.equal(window.customCardFeatures.filter((f) => f.type === 'zeptrion-air-scenes').length, 1);
  assert.equal(window.customStrategies.filter((s) => s.type === 'zeptrion-air').length, 1);
});

test('scenes feature is offered for Zeptrion blinds and lights only', () => {
  const { window } = load();
  const { supported } = window.customCardFeatures.find((f) => f.type === 'zeptrion-air-scenes');
  const hass = fakeHass();
  assert.equal(supported(hass, { entity_id: 'cover.blind' }), true);
  assert.equal(supported(hass, { entity_id: 'light.dimmer' }), true);
  assert.equal(supported(hass, { entity_id: 'light.other' }), false); // other integration
  assert.equal(supported(hass, { entity_id: 'sensor.x' }), false);
  assert.equal(supported({ entity_id: 'cover.blind' }), true); // older signature (stateObj)
});

test('scenes feature recalls the scene on the tile entity', () => {
  const { registry } = load();
  const Feature = registry.get('zeptrion-air-scenes');
  const hass = fakeHass();
  for (const [entityId, expected] of [
    ['cover.blind', ['zeptrion_air', 'blind_recall_s3', { entity_id: 'cover.blind' }]],
    ['light.dimmer', ['zeptrion_air', 'light_recall_scene', { entity_id: 'light.dimmer', scene: 3 }]],
  ]) {
    hass.calls.length = 0;
    const feature = new Feature();
    feature.setConfig({ type: 'custom:zeptrion-air-scenes' });
    feature.hass = hass;
    feature.context = { entity_id: entityId };
    const buttons = feature.shadowRoot.querySelectorAll('button');
    assert.equal(buttons.length, 4);
    buttons.find((b) => b.dataset.scene === '3').click();
    assert.deepEqual(plain(hass.calls), [expected]);
  }
});

test('scenes feature buttons are disabled while the entity is unavailable', () => {
  const { registry } = load();
  const Feature = registry.get('zeptrion-air-scenes');
  const hass = fakeHass();
  hass.states['cover.blind'].state = 'unavailable';
  const feature = new Feature();
  feature.setConfig({});
  feature.context = { entity_id: 'cover.blind' };
  feature.hass = hass;
  assert.ok(feature.shadowRoot.querySelectorAll('button').every((b) => b.disabled));
});

test('blinds card scene buttons call the recall actions on the cover', () => {
  const { registry } = load();
  const Card = registry.get('zeptrion-air-blinds-card');
  const hass = fakeHass();
  const card = new Card();
  card.setConfig({ entity: 'cover.blind' });
  card.hass = hass;
  card.shadowRoot.getElementById('scene2-btn').click();
  assert.deepEqual(plain(hass.calls.at(-1)), ['zeptrion_air', 'blind_recall_s2', { entity_id: 'cover.blind' }]);
});

test('dashboard strategy: views per floor, sections per area, fitting tile features', async () => {
  const { registry } = load();
  const Strategy = registry.get('ll-strategy-dashboard-zeptrion-air');
  const hass = fakeHass();
  const dashboard = await Strategy.generate({}, hass);
  assert.deepEqual(plain(dashboard.views.map((v) => v.title)), ['EG', 'UG', 'Other']);

  const living = dashboard.views[0].sections[0].cards;
  assert.equal(living[0].heading, 'Living');
  assert.deepEqual(plain(living.slice(1).map((c) => c.entity)), ['cover.blind', 'light.dimmer']); // no LED, no hue
  assert.deepEqual(plain(living[1].features.map((f) => f.type)), ['cover-open-close', 'cover-tilt', 'custom:zeptrion-air-scenes']);
  assert.deepEqual(plain(living[2].features.map((f) => f.type)), ['light-brightness', 'custom:zeptrion-air-scenes']);

  const cellar = dashboard.views[1].sections[0].cards; // entity area overrides the device area
  assert.deepEqual(plain(cellar.slice(1).map((c) => [c.entity, c.features.map((f) => f.type)])), [
    ['light.switch', ['custom:zeptrion-air-scenes']],
  ]);

  const other = dashboard.views[2].sections[0].cards; // no area at all
  assert.deepEqual(plain(other[1].features.map((f) => f.type)), ['cover-open-close', 'custom:zeptrion-air-scenes']);

  const noScenes = await Strategy.generate({ scenes: false }, hass);
  assert.ok(noScenes.views.flatMap((v) => v.sections.flatMap((s) => s.cards)).every(
    (c) => !(c.features || []).some((f) => f.type === 'custom:zeptrion-air-scenes'),
  ));
});

test('dashboard strategy without Zeptrion entities shows a hint', async () => {
  const { registry } = load();
  const Strategy = registry.get('ll-strategy-dashboard-zeptrion-air');
  const dashboard = await Strategy.generate({}, { entities: {}, states: {}, areas: {}, floors: {}, devices: {} });
  assert.equal(dashboard.views.length, 1);
  assert.equal(dashboard.views[0].cards[0].type, 'markdown');
});

test('dashboard strategy speaks the user language', async () => {
  const { registry } = load();
  const Strategy = registry.get('ll-strategy-dashboard-zeptrion-air');
  const hass = { ...fakeHass(), locale: { language: 'de' } };
  const dashboard = await Strategy.generate({}, hass);
  assert.equal(dashboard.views[2].title, 'Weitere');
  assert.equal(dashboard.views[2].sections[0].cards[0].heading, 'Weitere');
  const empty = await Strategy.generate({}, { language: 'fr', entities: {}, states: {}, areas: {}, floors: {}, devices: {} });
  assert.match(empty.views[0].cards[0].content, /Aucun store/);
});

test('the old blinds card warns once that it is deprecated', () => {
  const { registry, window, context } = load();
  const warnings = [];
  context.console = { ...console, warn: (message) => warnings.push(message) };
  vm.runInContext('console = this.console', context);
  const Card = registry.get('zeptrion-air-blinds-card');
  new Card().setConfig({ entity: 'cover.blind' });
  new Card().setConfig({ entity: 'cover.blind' });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /deprecated/);
  assert.match(window.customCards.find((c) => c.type === 'zeptrion-air-blinds-card').name, /deprecated/);
});
