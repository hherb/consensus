/**
 * @module structured-form
 * Generic schema-driven input form for human turns in structured phases
 * (issue #57). Renders one widget per JSON-schema property; falls back to a
 * guided-JSON textarea when spec.renderable is false. No external deps.
 */

/** Build a labelled wrapper for one field. */
function fieldRow(labelText, help, inputEl) {
    const row = document.createElement('div');
    row.className = 'sf-row';
    const label = document.createElement('label');
    label.className = 'sf-label';
    label.textContent = labelText;
    if (help) label.title = help;
    row.appendChild(label);
    if (help) {
        const h = document.createElement('div');
        h.className = 'sf-help';
        h.textContent = help;
        row.appendChild(h);
    }
    row.appendChild(inputEl);
    return row;
}

/** Create a single primitive/enum widget from a property subschema. */
function widgetFor(key, prop) {
    if (Array.isArray(prop.enum)) {
        const sel = document.createElement('select');
        sel.dataset.key = key;
        for (const opt of prop.enum) {
            const o = document.createElement('option');
            o.value = opt; o.textContent = opt;
            sel.appendChild(o);
        }
        return sel;
    }
    if (prop.type === 'number' || prop.type === 'integer') {
        const inp = document.createElement('input');
        inp.type = 'number';
        inp.dataset.key = key;
        inp.dataset.jsontype = prop.type;
        if (prop.minimum !== undefined) inp.min = prop.minimum;
        if (prop.maximum !== undefined) inp.max = prop.maximum;
        if (prop.type === 'number') inp.step = 'any';
        return inp;
    }
    if (prop.type === 'boolean') {
        const inp = document.createElement('input');
        inp.type = 'checkbox';
        inp.dataset.key = key;
        inp.dataset.jsontype = 'boolean';
        return inp;
    }
    // string (default), long fields become textareas
    const long = key === 'reasoning' || key === 'position' || key === 'claim';
    const inp = document.createElement(long ? 'textarea' : 'input');
    if (!long) inp.type = 'text';
    inp.dataset.key = key;
    inp.dataset.jsontype = 'string';
    return inp;
}

/**
 * Render the full form element for a structured-turn input spec.
 * @param {{tool_name: string, description?: string, schema: object, renderable: boolean}} spec
 * @param {{onSubmit: (payload: object, errEl: HTMLElement) => void, onSkip: () => void}} callbacks
 * @returns {HTMLElement}
 */
export function renderStructuredForm(spec, { onSubmit, onSkip }) {
    const form = document.createElement('div');
    form.className = 'structured-form';

    const title = document.createElement('div');
    title.className = 'sf-title';
    title.textContent = spec.description || spec.tool_name;
    form.appendChild(title);

    const body = document.createElement('div');
    body.className = 'sf-body';
    form.appendChild(body);

    const errEl = document.createElement('div');
    errEl.className = 'sf-error';
    errEl.setAttribute('role', 'alert');
    form.appendChild(errEl);

    let collect;
    if (spec.renderable) {
        collect = renderFields(body, spec.schema);
    } else {
        collect = renderGuidedJson(body, spec.schema);
    }

    const bar = document.createElement('div');
    bar.className = 'sf-actions';
    const submit = document.createElement('button');
    submit.className = 'btn btn-primary sf-submit';
    submit.textContent = 'Submit';
    const skip = document.createElement('button');
    skip.className = 'btn btn-outline sf-skip';
    skip.textContent = 'Skip turn';
    submit.addEventListener('click', () => {
        errEl.textContent = '';
        const { payload, error } = collect();
        if (error) { errEl.textContent = error; return; }
        // Guard against double-submits while the request is in flight; the
        // form is torn down on the next render anyway once the turn moves
        // on, so re-enabling here only matters on the error path.
        submit.disabled = true;
        skip.disabled = true;
        Promise.resolve(onSubmit(payload, errEl)).finally(() => {
            submit.disabled = false;
            skip.disabled = false;
        });
    });
    skip.addEventListener('click', () => {
        submit.disabled = true;
        skip.disabled = true;
        Promise.resolve(onSkip()).finally(() => {
            submit.disabled = false;
            skip.disabled = false;
        });
    });
    bar.appendChild(submit);
    bar.appendChild(skip);
    form.appendChild(bar);
    return form;
}

/** Renderable path: one widget per top-level property. Returns a collector. */
function renderFields(body, schema) {
    const props = schema.properties || {};
    const required = schema.required || [];
    const collectors = [];
    for (const [key, prop] of Object.entries(props)) {
        if (prop.type === 'array') {
            collectors.push(renderArray(body, key, prop));
        } else if (prop.type === 'object' && prop.properties) {
            collectors.push(renderObject(body, key, prop));
        } else {
            const w = widgetFor(key, prop);
            body.appendChild(fieldRow(labelFor(key, required), prop.description, w));
            collectors.push(() => readWidget(w));
        }
    }
    return () => assemble(collectors, required);
}

function labelFor(key, required) {
    return required.includes(key) ? `${key} *` : key;
}

/** Read a primitive widget into [key, value] or throw a message. */
function readWidget(w) {
    const key = w.dataset.key;
    if (w.tagName === 'SELECT') return [key, w.value];
    if (w.dataset.jsontype === 'boolean') return [key, w.checked];
    const raw = w.value.trim();
    if (raw === '') return [key, undefined];
    if (w.dataset.jsontype === 'number') return [key, Number(raw)];
    if (w.dataset.jsontype === 'integer') return [key, parseInt(raw, 10)];
    return [key, raw];
}

function assemble(collectors, required) {
    const payload = {};
    for (const c of collectors) {
        const entry = c();
        if (entry && entry.error) return { error: entry.error };
        const [k, v] = entry;
        if (v !== undefined) payload[k] = v;
    }
    for (const r of required) {
        if (!(r in payload)) return { error: `Please fill in '${r}'.` };
    }
    return { payload };
}

/** Array of primitives or one-level objects. Returns a collector -> [key,val]. */
function renderArray(body, key, prop) {
    const wrap = document.createElement('div');
    wrap.className = 'sf-array';
    const rows = [];
    const items = prop.items || {};
    const addRow = () => {
        const row = document.createElement('div');
        row.className = 'sf-array-row';
        const inputs = [];
        if (items.type === 'object' && items.properties) {
            for (const [ik, ip] of Object.entries(items.properties)) {
                const w = widgetFor(ik, ip);
                w.placeholder = ik;
                row.appendChild(w);
                inputs.push(w);
            }
        } else {
            const w = widgetFor(key, items);
            row.appendChild(w);
            inputs.push(w);
        }
        const del = document.createElement('button');
        del.textContent = '×';
        del.className = 'btn btn-outline btn-sm sf-del';
        del.addEventListener('click', () => { wrap.removeChild(row);
            rows.splice(rows.indexOf(entry), 1); });
        row.appendChild(del);
        const entry = { row, inputs, single: items.type !== 'object' };
        rows.push(entry);
        wrap.insertBefore(row, addBtn);
    };
    const addBtn = document.createElement('button');
    addBtn.textContent = `+ add ${key}`;
    addBtn.className = 'btn btn-outline btn-sm sf-add';
    addBtn.addEventListener('click', addRow);
    body.appendChild(fieldRow(key, prop.description, wrap));
    wrap.appendChild(addBtn);
    addRow();
    return () => {
        const arr = [];
        for (const e of rows) {
            if (e.single) {
                const [, v] = readWidget(e.inputs[0]);
                if (v !== undefined) arr.push(v);
            } else {
                const obj = {};
                for (const w of e.inputs) {
                    const [k, v] = readWidget(w);
                    if (v !== undefined) obj[k] = v;
                }
                if (Object.keys(obj).length) arr.push(obj);
            }
        }
        return [key, arr.length ? arr : undefined];
    };
}

/** Resolved nested object (e.g. expanded belief map). collector -> [key,val]. */
function renderObject(body, key, prop) {
    const wrap = document.createElement('div');
    wrap.className = 'sf-object';
    const widgets = [];
    for (const [ik, ip] of Object.entries(prop.properties)) {
        const w = widgetFor(ik, ip);
        wrap.appendChild(fieldRow(ik, ip.description, w));
        widgets.push(w);
    }
    body.appendChild(fieldRow(key, prop.description, wrap));
    return () => {
        const obj = {};
        for (const w of widgets) {
            const [k, v] = readWidget(w);
            if (v !== undefined) obj[k] = v;
        }
        return [key, obj];
    };
}

/** Guided-JSON fallback: schema shown + a JSON textarea. */
function renderGuidedJson(body, schema) {
    const note = document.createElement('div');
    note.className = 'sf-help';
    note.textContent = 'Enter your response as JSON matching this schema:';
    const pre = document.createElement('pre');
    pre.className = 'sf-schema';
    pre.textContent = JSON.stringify(schema, null, 2);
    const ta = document.createElement('textarea');
    ta.className = 'sf-json';
    ta.value = skeletonFor(schema);
    body.appendChild(note);
    body.appendChild(pre);
    body.appendChild(ta);
    return () => {
        try {
            return { payload: JSON.parse(ta.value) };
        } catch (e) {
            return { error: 'Invalid JSON: ' + e.message };
        }
    };
}

/** Minimal JSON skeleton derived from a schema's top-level properties. */
function skeletonFor(schema) {
    const obj = {};
    for (const [k, p] of Object.entries(schema.properties || {})) {
        obj[k] = p.type === 'object' ? {} : p.type === 'array' ? [] :
            p.type === 'number' || p.type === 'integer' ? 0 : '';
    }
    return JSON.stringify(obj, null, 2);
}
