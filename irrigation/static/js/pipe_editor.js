// Irrigation Planner - Pipe (Main Pipe / Zone Pipe) map editor
// Adds in-map controls: Add, (optional) Trace Main/Zone Pipe, Edit, Rename, Remove.
// Selection-aware enable/disable for Add / Edit / Rename / Remove.
// Depends on map.js globals (map, drawnItems) and the shared pipe editor modals.

(function () {
    let cfg = null;
    let activeCreatedHandler = null;
    const btnRefs = {};

    function el(id) {
        return id ? document.getElementById(id) : null;
    }

    function checkboxSelector() {
        return (cfg && cfg.checkboxSelector) || '.pipe-checkbox';
    }

    function allCheckboxes() {
        return Array.from(document.querySelectorAll(checkboxSelector()));
    }

    function checkedCheckboxes() {
        return allCheckboxes().filter(function (cb) { return cb.checked; });
    }

    function itemLabel() {
        return (cfg && cfg.item) || { en: 'pipe', ar: 'أنبوب' };
    }

    function showModal(id) {
        const modal = el(id);
        if (modal && window.bootstrap) {
            bootstrap.Modal.getOrCreateInstance(modal).show();
        }
    }

    function tracePolyline(onComplete) {
        if (typeof map === 'undefined' || !map) return;
        if (activeCreatedHandler) {
            map.off(L.Draw.Event.CREATED, activeCreatedHandler);
            activeCreatedHandler = null;
        }
        const drawer = new L.Draw.Polyline(map, { allowIntersection: false });
        drawer.enable();
        activeCreatedHandler = function (event) {
            map.off(L.Draw.Event.CREATED, activeCreatedHandler);
            activeCreatedHandler = null;
            if (typeof drawnItems !== 'undefined' && drawnItems) {
                drawnItems.clearLayers();
                drawnItems.addLayer(event.layer);
            }
            const coords = event.layer.toGeoJSON().geometry.coordinates;
            onComplete(coords, JSON.stringify(coords));
        };
        map.on(L.Draw.Event.CREATED, activeCreatedHandler);
    }

    function openAdd() {
        if (!cfg) return;
        if (cfg.traceAdd) {
            // In-map "+": ask for the diameter (and zone), then start tracing.
            const title = el('pipeTraceAddTitle');
            if (title && cfg.traceAdd.title) title.innerHTML = cfg.traceAdd.title;
            const zoneWrap = el('pipeTraceZoneWrap');
            if (zoneWrap) zoneWrap.hidden = !cfg.traceAdd.zone;
            const diameter = el('pipeTraceDiameter');
            if (diameter && cfg.traceAdd.diameter != null) diameter.value = cfg.traceAdd.diameter;
            showModal('pipeTraceAddModal');
            return;
        }
        showModal(cfg.addModalId);
    }

    function submitTracedAdd(coordsStr, diameter, zone) {
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = cfg.urls.add;
        const addField = function (name, value) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = name;
            input.value = value;
            form.appendChild(input);
        };
        addField('coordinates', coordsStr);
        if (diameter) addField('diameter', diameter);
        if (zone) addField('zone_id', zone);
        document.body.appendChild(form);
        form.submit();
    }

    function startTraceAdd() {
        if (!cfg || !cfg.traceAdd) return;
        const diameter = el('pipeTraceDiameter') ? el('pipeTraceDiameter').value : '';
        const zone = (cfg.traceAdd.zone && el('pipeTraceZone')) ? el('pipeTraceZone').value : '';
        tracePolyline(function (coords, coordsStr) {
            submitTracedAdd(coordsStr, diameter, zone);
        });
    }

    function wireTraceAdd() {
        if (!cfg || !cfg.traceAdd) return;
        const startButton = el('pipeTraceStart');
        if (startButton && !startButton.dataset.wired) {
            startButton.dataset.wired = 'true';
            startButton.addEventListener('click', function () {
                const modalElement = el('pipeTraceAddModal');
                if (modalElement && window.bootstrap) {
                    const instance = bootstrap.Modal.getOrCreateInstance(modalElement);
                    modalElement.addEventListener('hidden.bs.modal', startTraceAdd, { once: true });
                    instance.hide();
                } else {
                    startTraceAdd();
                }
            });
        }
    }

    function traceMain() {
        if (!cfg || !cfg.trace || !cfg.trace.main) return;
        const target = cfg.trace.main;
        tracePolyline(function (coords, coordsStr) {
            const field = el(target.fieldId);
            if (field) field.value = coordsStr;
            showModal(target.modalId);
        });
    }

    function traceZone() {
        if (!cfg || !cfg.trace || !cfg.trace.zone) return;
        const target = cfg.trace.zone;
        tracePolyline(function (coords, coordsStr) {
            const field = el(target.fieldId);
            if (field) field.value = coordsStr;
            showModal(target.modalId);
        });
    }

    function openEdit() {
        if (!cfg) return;
        const selected = checkedCheckboxes();
        if (selected.length !== 1) {
            alert('Select exactly one ' + itemLabel().en + ' to edit. / اختر عنصرا واحدا للتعديل.');
            return;
        }
        const cb = selected[0];
        el('pipeEditId').value = cb.value;
        el('pipeEditDiameter').value = cb.dataset.diameter || '';
        el('pipeEditCoords').value = cb.dataset.coords || '';
        el('pipeEditForm').action = cfg.urls.update;
        showModal('pipeEditModal');
    }

    function openRenameById(id) {
        if (!cfg) return;
        const cb = allCheckboxes().find(function (input) { return input.value === String(id); });
        el('pipeRenameId').value = id;
        el('pipeRenameNameEn').value = cb ? (cb.dataset.nameEn || '') : '';
        el('pipeRenameNameAr').value = cb ? (cb.dataset.nameAr || '') : '';
        el('pipeRenameForm').action = cfg.urls.rename;
        showModal('pipeRenameModal');
    }

    function openRename() {
        const selected = checkedCheckboxes();
        if (selected.length !== 1) {
            alert('Select exactly one ' + itemLabel().en + ' to rename. / اختر عنصرا واحدا لإعادة التسمية.');
            return;
        }
        openRenameById(selected[0].value);
    }

    function nameForId(id) {
        const cb = allCheckboxes().find(function (input) { return input.value === String(id); });
        return cb ? { en: cb.dataset.nameEn || '', ar: cb.dataset.nameAr || '' } : { en: '', ar: '' };
    }

    function removeByIds(ids) {
        if (!cfg || !ids.length) return;

        const names = ids.map(nameForId);
        const label = itemLabel();
        const nameEn = names.map(function (n) { return n.en; }).filter(Boolean).join(', ') || label.en;
        const nameAr = names.map(function (n) { return n.ar; }).filter(Boolean).join('، ') || label.ar;

        const proceed = function () {
            if (cfg.removeAjax === false) {
                const form = el('pipeRemoveForm');
                form.action = cfg.urls.remove;
                form.querySelectorAll('input[name="pipe_ids"]').forEach(function (input) { input.remove(); });
                ids.forEach(function (id) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'pipe_ids';
                    input.value = id;
                    form.appendChild(input);
                });
                form.requestSubmit();
                return;
            }
            ajaxRemove(ids);
        };

        if (window.confirmDelete) {
            window.confirmDelete(nameEn, nameAr, proceed);
        } else if (window.confirm('Delete ' + nameEn + ' ?')) {
            proceed();
        }
    }

    function ajaxRemove(ids) {
        const body = new URLSearchParams();
        ids.forEach(function (id) { body.append('pipe_ids', id); });
        fetch(cfg.urls.remove, {
            method: 'POST',
            body: body,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
        .then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        })
        .then(function () { applyPipeRemoval(ids); })
        .catch(function () { window.location.reload(); });
    }

    function applyPipeRemoval(ids) {
        const idSet = {};
        ids.forEach(function (id) { idSet[String(id)] = true; });

        allCheckboxes().forEach(function (cb) {
            if (idSet[String(cb.value)]) {
                const tr = cb.closest('tr');
                if (tr) tr.remove();
            }
        });

        if (typeof featureLayer !== 'undefined' && featureLayer) {
            featureLayer.eachLayer(function (layer) {
                const props = layer.feature && layer.feature.properties;
                if (props && idSet[String(props.id)]) featureLayer.removeLayer(layer);
            });
        }

        const selectAll = el('selectAllPipes');
        if (selectAll) selectAll.checked = false;

        if (!allCheckboxes().length) showEmptyTableState();
        updateSelectionState();
    }

    function showEmptyTableState() {
        const table = el('pipeTable');
        if (!table) return;
        const tbody = table.querySelector('tbody');
        if (!tbody || tbody.querySelector('tr')) return;
        const colspan = table.querySelectorAll('thead th').length || 1;
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = colspan;
        cell.className = 'text-center text-muted py-3';
        cell.textContent = emptyMessage();
        row.appendChild(cell);
        tbody.appendChild(row);
    }

    function emptyMessage() {
        return (cfg && cfg.emptyLabel) || 'No pipes yet / لا توجد أنابيب بعد';
    }

    function removeSelected() {
        const selected = checkedCheckboxes();
        if (!selected.length) {
            alert('Select at least one ' + itemLabel().en + ' first. / اختر عنصرا واحدا على الأقل أولا.');
            return;
        }
        removeByIds(selected.map(function (cb) { return cb.value; }));
    }

    function setButtonEnabled(role, enabled) {
        const mapButton = btnRefs[role];
        if (mapButton) {
            mapButton.disabled = !enabled;
        }
        const selector = cfg.selectors && cfg.selectors[role];
        if (selector) {
            document.querySelectorAll(selector).forEach(function (toolbarButton) {
                toolbarButton.disabled = !enabled;
            });
        }
    }

    function updateSelectionState() {
        if (!cfg) return;
        const count = checkedCheckboxes().length;
        setButtonEnabled('add', count === 0);
        setButtonEnabled('edit', count === 1);
        setButtonEnabled('rename', count === 1);
        setButtonEnabled('remove', count >= 1);
    }

    function buildControl() {
        if (typeof map === 'undefined' || !map) return;

        const buttons = [
            { role: 'add', icon: 'bi-plus-circle', title: 'Add / إضافة', onClick: openAdd }
        ];

        if (cfg.traceButtons !== false) {
            if (cfg.trace && cfg.trace.main) {
                buttons.push({ icon: 'bi-water', title: 'Trace Main Pipe / رسم الأنبوب الرئيسي', onClick: traceMain });
            }
            if (cfg.trace && cfg.trace.zone) {
                buttons.push({ icon: 'bi-droplet', title: 'Trace Zone Pipe / رسم أنبوب المنطقة', onClick: traceZone });
            }
        }

        buttons.push(
            { role: 'edit', icon: 'bi-pencil', title: 'Edit / Change / تعديل', onClick: openEdit },
            { role: 'rename', icon: 'bi-input-cursor-text', title: 'Rename / إعادة تسمية', onClick: openRename },
            { role: 'remove', icon: 'bi-trash', title: 'Remove / إزالة', onClick: removeSelected }
        );

        // Add / Edit / Rename / Remove are action buttons: left side, below the
        // zoom and draw controls.
        const control = L.control({ position: 'topleft' });
        control.onAdd = function () {
            const group = L.DomUtil.create('div', 'leaflet-bar geometry-map-select geometry-map-actions pipe-map-actions');
            L.DomEvent.disableClickPropagation(group);
            L.DomEvent.disableScrollPropagation(group);
            buttons.forEach(function (button) {
                const actionButton = L.DomUtil.create('button', 'geometry-map-select-button geometry-map-action-button', group);
                actionButton.type = 'button';
                actionButton.title = button.title;
                actionButton.setAttribute('aria-label', button.title);
                actionButton.innerHTML = '<i class="bi ' + button.icon + '"></i>';
                if (button.role) {
                    btnRefs[button.role] = actionButton;
                }
                L.DomEvent.on(actionButton, 'click', function (event) {
                    L.DomEvent.stop(event);
                    if (!actionButton.disabled) button.onClick();
                });
            });
            return group;
        };
        control.addTo(map);
    }

    window.initPipeEditor = function (config) {
        cfg = config || {};
        buildControl();
        wireTraceAdd();

        document.addEventListener('change', function (event) {
            if (event.target && event.target.matches && event.target.matches(checkboxSelector())) {
                updateSelectionState();
            }
        });

        updateSelectionState();
    };

    window.toggleAllPipes = function (source) {
        allCheckboxes().forEach(function (cb) {
            cb.checked = source.checked;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
        });
    };

    window.openPipeRename = function (id) { openRenameById(id); };
    window.removePipeById = function (id) { removeByIds([id]); };
    window.openPipeEdit = function () { openEdit(); };
    window.pipeEditorRenameSelected = function () { openRename(); };
    window.pipeEditorRemoveSelected = function () { removeSelected(); };
})();