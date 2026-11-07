"""Team-mix panel for the coordinator viewer: four linked family sliders, presets,
tolerance and per-family model tiers. Authored by Codex (task T3); markup only."""

HTML = r'''
  <section id="team-mix" class="panel session" aria-labelledby="mix-title">
    <h2 id="mix-title">Team mix</h2>
    <p id="mix-help" class="muted smalltext">
      Set each family's share. Moving a slider adjusts the other shares to keep
      the total at 100%. Changes apply only when you save.
    </p>

    <fieldset class="mix-scope">
      <legend>Edit settings for</legend>
      <label><input type="radio" name="mix-scope" value="global" checked> New runs</label>
      <label><input id="mix-run-scope" type="radio" name="mix-scope" value="run" disabled> Selected run</label>
    </fieldset>

    <fieldset id="mix-editor" class="mix-editor" disabled aria-describedby="mix-help mix-note">
      <legend class="sr-only">Team allocation</legend>
      <div class="mix-presets" role="group" aria-label="Allocation presets">
        <button type="button" data-mix-preset="balanced" aria-pressed="false">Balanced</button>
        <button type="button" data-mix-preset="code" aria-pressed="false">Code-heavy</button>
        <button type="button" data-mix-preset="visual" aria-pressed="false">Visual</button>
        <button type="button" data-mix-preset="research" aria-pressed="false">Research</button>
        <span id="mix-custom" hidden>Custom</span>
      </div>

      <div id="mix-rows" class="mix-rows"></div>

      <label class="mix-tolerance" for="mix-tolerance">
        Tolerance
        <select id="mix-tolerance">
          <option value="5">±5 points</option>
          <option value="10">±10 points</option>
          <option value="15">±15 points</option>
          <option value="20">±20 points</option>
          <option value="25">±25 points</option>
        </select>
      </label>
      <p class="mix-summary">Total: <strong id="mix-total">100%</strong></p>
    </fieldset>

    <p id="mix-note" class="muted smalltext" role="status" aria-live="polite">
      Loading team mix…
    </p>
    <div class="mix-actions">
      <button id="mix-save" type="button" class="primary" hidden disabled
              aria-describedby="mix-note">Save team mix</button>
      <button id="mix-reload" type="button">Reload saved settings</button>
    </div>
  </section>
'''

STYLE = r'''
.mix-scope,
.mix-editor {
  min-width: 0;
  margin: 12px 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
}
.mix-scope {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.mix-scope label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.mix-presets,
.mix-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.mix-presets button,
.mix-actions button {
  min-height: 40px;
}
.mix-presets button[aria-pressed="true"] {
  background: var(--accent);
  color: var(--accent-ink);
}
.mix-rows {
  display: grid;
  gap: 12px;
  margin: 16px 0;
}
.mix-row {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) minmax(120px, 2fr) minmax(120px, 1.5fr);
  align-items: center;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.mix-name {
  font-weight: 650;
}
.mix-worker {
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 400;
}
.mix-badge {
  margin-left: 6px;
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--chip);
  font-size: 12px;
}
.mix-share {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 4ch;
  align-items: center;
  gap: 10px;
}
.mix-share input {
  width: 100%;
  min-width: 0;
  min-height: 40px;
  accent-color: var(--accent);
}
.mix-share output {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.mix-tier {
  display: grid;
  min-width: 0;
  gap: 4px;
}
.mix-tier select,
.mix-tolerance select {
  max-width: 100%;
  min-width: 0;
  min-height: 40px;
  background: var(--inset);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.mix-tolerance {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.mix-summary {
  margin-bottom: 0;
}
.mix-error {
  color: var(--bad);
}
.mix-scope input:focus-visible,
.mix-editor input:focus-visible,
.mix-editor select:focus-visible,
.mix-presets button:focus-visible,
.mix-actions button:focus-visible {
  outline: 3px solid var(--accent);
  outline-offset: 3px;
}
.mix-editor:disabled {
  opacity: .65;
}
@media (max-width: 650px) {
  .mix-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 6px;
  }
  .mix-tier select {
    width: 100%;
  }
}
'''

JS = r'''
  // Team mix (see team_mix.py for the slider contract).
  const MIX_FAMILIES = ['OpenAI', 'Anthropic', 'xAI', 'Google'];
  const MIX_WORKERS = ['Codex', 'Claude', 'Grok', 'Gemini'];
  const MIX_TIERS = ['best', 'balanced', 'economy'];
  const MIX_PRESETS = {
    balanced: [25, 25, 25, 25],
    code: [30, 40, 15, 15],
    visual: [40, 20, 15, 25],
    research: [25, 20, 20, 35]
  };

  // Named, pure function; does not mutate shares.
  function teamMixRebalance(shares, family, value) {
    if (!MIX_FAMILIES.includes(family) || !Number.isFinite(value)) {
      throw new TypeError('Choose a valid family and finite share.');
    }
    value = Math.max(5, Math.min(85, Math.floor(value + 0.5)));
    const others = MIX_FAMILIES.filter(name => name !== family);
    let weights = others.map(name => shares[name] - 5);
    if (weights.every(weight => weight === 0)) weights = [1, 1, 1];

    const extra = 100 - value - 15;
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
    const splits = weights.map(weight => Math.floor(extra * weight / totalWeight));
    const order = weights.map((weight, index) => ({
      index,
      remainder: (extra * weight) % totalWeight
    })).sort((a, b) => b.remainder - a.remainder || a.index - b.index);

    const remaining = extra - splits.reduce((sum, split) => sum + split, 0);
    for (let index = 0; index < remaining; index++) {
      splits[order[index].index]++;
    }

    const result = {};
    for (const name of MIX_FAMILIES) {
      result[name] = name === family ? value : 5 + splits[others.indexOf(name)];
    }
    return result;
  }

  function mixEditable(mix) {
    return {
      shares: {...mix.shares},
      preset: mix.preset,
      tolerance: mix.tolerance,
      tiers: {...mix.tiers}
    };
  }

  function mixEqual(a, b) {
    return a.preset === b.preset && a.tolerance === b.tolerance &&
      MIX_FAMILIES.every(name =>
        a.shares[name] === b.shares[name] && a.tiers[name] === b.tiers[name]);
  }

  let mixScope = 'global';
  let mixEntry = null;
  let mixLoading = false;
  let mixSaving = false;
  let mixLoadSequence = 0;
  let mixLoadError = '';
  const mixEntries = new Map();
  const mixControls = new Map();

  function mixTarget() {
    const run = mixScope === 'run' ? $('run').value : null;
    return {scope: mixScope, run, key: JSON.stringify([mixScope, run])};
  }

  function mixDirty(entry) {
    return Boolean(entry && !mixEqual(entry.draft, mixEditable(entry.state.mix)));
  }

  // Create rows once so slider updates preserve keyboard focus.
  MIX_FAMILIES.forEach((family, index) => {
    const row = document.createElement('div');
    row.className = 'mix-row';

    const name = document.createElement('div');
    name.className = 'mix-name';
    const label = document.createElement('label');
    label.htmlFor = 'mix-share-' + index;
    label.textContent = family;
    const badge = document.createElement('span');
    badge.className = 'mix-badge';
    badge.textContent = 'Lead';
    badge.hidden = true;
    const worker = document.createElement('small');
    worker.className = 'mix-worker';
    worker.textContent = MIX_WORKERS[index];
    name.append(label, badge, worker);

    const share = document.createElement('div');
    share.className = 'mix-share';
    const slider = document.createElement('input');
    slider.id = label.htmlFor;
    slider.type = 'range';
    slider.min = '5';
    slider.max = '85';
    slider.step = '1';
    slider.value = '25';
    const output = document.createElement('output');
    output.setAttribute('for', slider.id);
    output.textContent = '25%';
    share.append(slider, output);

    const tierLabel = document.createElement('label');
    tierLabel.className = 'mix-tier';
    tierLabel.textContent = 'Model tier';
    const tier = document.createElement('select');
    tier.setAttribute('aria-label', family + ' model tier');
    for (const value of MIX_TIERS) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value[0].toUpperCase() + value.slice(1);
      tier.append(option);
    }
    tierLabel.append(tier);
    row.append(name, share, tierLabel);
    $('mix-rows').append(row);
    mixControls.set(family, {slider, output, badge, tier});

    slider.addEventListener('input', () => {
      if (!mixEntry || mixLoading || mixSaving) return;
      mixEntry.draft.shares = teamMixRebalance(
        mixEntry.draft.shares, family, Number(slider.value));
      mixEntry.draft.preset = null;
      mixEntry.error = '';
      renderTeamMix();
    });
    tier.addEventListener('change', () => {
      if (!mixEntry || mixLoading || mixSaving) return;
      mixEntry.draft.tiers[family] = tier.value;
      mixEntry.error = '';
      renderTeamMix();
    });
  });

  function renderTeamMix() {
    const target = mixTarget();
    const blocked = mixLoading || mixSaving || !mixEntry;
    $('mix-editor').disabled = blocked;
    for (const radio of document.querySelectorAll('input[name="mix-scope"]')) {
      radio.checked = radio.value === mixScope;
      radio.disabled = mixSaving || (radio.value === 'run' && !$('run').value);
    }

    const changed = mixDirty(mixEntry);
    $('mix-save').hidden = !changed && !mixSaving;
    $('mix-save').disabled = blocked || !changed;
    $('mix-reload').disabled = mixLoading || mixSaving;
    $('mix-save').textContent = target.scope === 'run'
      ? 'Save run team mix' : 'Save new-run defaults';

    if (mixEntry) {
      const {draft, state} = mixEntry;
      for (const family of MIX_FAMILIES) {
        const controls = mixControls.get(family);
        controls.slider.value = draft.shares[family];
        controls.slider.setAttribute('aria-valuetext', draft.shares[family] + ' percent');
        controls.output.textContent = draft.shares[family] + '%';
        controls.badge.hidden = target.scope !== 'run' || state.lead_family !== family;
        for (const option of controls.tier.options) {
          const title = option.value[0].toUpperCase() + option.value.slice(1);
          const model = state.tier_models?.[family]?.[option.value];
          option.textContent = title + (model ? ' — ' + model : '');
        }
        controls.tier.value = draft.tiers[family];
      }
      // The persisted contract permits any integer tolerance from 5 through 25.
      // Preserve a CLI-set value even when it is outside the five standard choices.
      const tolerance = $('mix-tolerance');
      tolerance.querySelectorAll('[data-mix-extra]').forEach(option => option.remove());
      if (![5, 10, 15, 20, 25].includes(draft.tolerance)) {
        const option = document.createElement('option');
        option.value = draft.tolerance;
        option.textContent = '±' + draft.tolerance + ' points';
        option.dataset.mixExtra = 'true';
        tolerance.append(option);
      }
      tolerance.value = String(draft.tolerance);
      $('mix-total').textContent =
        MIX_FAMILIES.reduce((sum, family) => sum + draft.shares[family], 0) + '%';
      $('mix-custom').hidden = draft.preset !== null;
      document.querySelectorAll('[data-mix-preset]').forEach(button => {
        button.setAttribute('aria-pressed',
          String(button.dataset.mixPreset === draft.preset));
      });
    }

    const error = mixLoadError || mixEntry?.error || '';
    $('mix-note').classList.toggle('mix-error', Boolean(error));
    if (mixSaving) {
      $('mix-note').textContent = 'Saving team mix…';
    } else if (mixLoading) {
      $('mix-note').textContent = 'Loading team mix…';
    } else if (!mixEntry) {
      $('mix-note').textContent = error || 'Team mix unavailable. Reload to try again.';
    } else {
      const destination = target.scope === 'run'
        ? 'run ' + target.run : 'new runs';
      const summary = changed
        ? 'Unsaved changes. Save will apply this team mix to ' + destination + '.'
        : 'Showing saved team mix for ' + destination + '.';
      const history = target.scope === 'run'
        ? ' History entries: ' + mixEntry.state.history_count + '.' : '';
      $('mix-note').textContent = [error, summary + history].filter(Boolean).join(' ');
    }
  }

  async function loadTeamMix(discardDraft = false) {
    const target = mixTarget();
    const sequence = ++mixLoadSequence;
    mixLoading = true;
    mixLoadError = '';
    mixEntry = null;
    renderTeamMix();
    try {
      const state = await api('/api/team-mix' +
        (target.scope === 'run' ? '?run=' + encodeURIComponent(target.run) : ''));
      if (sequence !== mixLoadSequence) return;
      if (state.scope !== target.scope ||
          (target.scope === 'run' && state.run_id !== target.run)) {
        throw new Error('The server returned settings for a different scope.');
      }
      const previous = mixEntries.get(target.key);
      const preserve = !discardDraft && mixDirty(previous);
      mixEntry = {
        state,
        draft: preserve ? previous.draft : mixEditable(state.mix),
        error: ''
      };
      mixEntries.set(target.key, mixEntry);
    } catch (error) {
      if (sequence !== mixLoadSequence) return;
      mixLoadError = error.message;
    } finally {
      if (sequence === mixLoadSequence) {
        mixLoading = false;
        renderTeamMix();
      }
    }
  }

  async function saveTeamMix() {
    if (mixSaving || mixLoading || !mixDirty(mixEntry)) return;
    const target = mixTarget();
    const entry = mixEntry;
    const draft = mixEditable(entry.draft);
    const body = {
      scope: target.scope,
      tolerance: draft.tolerance,
      tiers: draft.tiers
    };
    if (target.scope === 'run') body.run = target.run;
    if (draft.preset === null) {
      body.shares = draft.shares;
      body.preset = null;
    } else {
      body.preset = draft.preset;
    }

    mixSaving = true;
    entry.error = '';
    renderTeamMix();
    try {
      const state = await api('/api/team-mix', body);
      if (state.scope !== target.scope ||
          (target.scope === 'run' && state.run_id !== target.run)) {
        throw new Error('Unexpected save response. Reload saved settings to verify.');
      }
      entry.state = state;
      entry.draft = mixEditable(state.mix);
    } catch (error) {
      entry.error = error.message;
    } finally {
      mixSaving = false;
      // The shared run picker can change while a save is in flight.
      if (mixTarget().key !== target.key) {
        await loadTeamMix();
      } else {
        renderTeamMix();
      }
    }
  }

  document.querySelectorAll('[data-mix-preset]').forEach(button => {
    button.addEventListener('click', () => {
      if (!mixEntry || mixLoading || mixSaving) return;
      const preset = button.dataset.mixPreset;
      mixEntry.draft.preset = preset;
      mixEntry.draft.shares = Object.fromEntries(
        MIX_FAMILIES.map((family, index) => [family, MIX_PRESETS[preset][index]]));
      mixEntry.error = '';
      renderTeamMix();
    });
  });

  $('mix-tolerance').addEventListener('change', () => {
    if (!mixEntry || mixLoading || mixSaving) return;
    mixEntry.draft.tolerance = Number($('mix-tolerance').value);
    mixEntry.error = '';
    renderTeamMix();
  });

  document.querySelectorAll('input[name="mix-scope"]').forEach(radio => {
    radio.addEventListener('change', () => {
      if (!radio.checked || mixSaving) return;
      mixScope = radio.value;
      void loadTeamMix();
    });
  });

  $('run').addEventListener('change', () => {
    if (mixScope === 'run') {
      if (!$('run').value) mixScope = 'global';
      void loadTeamMix();
    } else {
      renderTeamMix();
    }
  });

  $('mix-save').addEventListener('click', saveTeamMix);
  $('mix-reload').addEventListener('click', () => {
    if (mixDirty(mixEntry) &&
        !window.confirm('Discard unsaved changes for this scope and reload saved settings?')) {
      return;
    }
    void loadTeamMix(true);
  });

  void loadTeamMix();
'''
