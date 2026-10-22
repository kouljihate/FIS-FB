// Irrigation Planner - Map JavaScript
// Uses Leaflet.js with Leaflet.draw for editing

let map = null;
let drawLayer = null;
let featureLayer = null;
let drawnItems = null;
let mapSelectionMode = false;
let mapRulerActive = false;
let sectorSelection = null;
let labelLayers = null;
let currentGeojsonUrl = null;
let currentComponentFilters = [];// Feature types the page never draws (a components tab only shows the fitting it
// owns).  They are dropped before the GeoJSON reaches the map, so no layer, popup
// or label of that kind can appear.
let hiddenFeatureTypes = [];
// On-demand geometry: only the categories shown at load are fetched, the rest
// are requested the first time the user switches them on.  Drawing every tree
// and dripline of a large orchard up front froze the browser.
let lazyGeometry = false;
let lazyGeometryLoading = null;
const loadedFeatureTypes = new Set();
// Sector scope (Map Overview): one sector's marks on screen, every other
// sector kept only as a dashed boundary.  null shows the whole project.
let sectorScope = null;
let sectorScopeFilterState = null;
let initialFilterKeys = null;
// Page callback told whenever a feature is selected or deselected on the map.
let mapSelectionListener = null;
// Tree labels are drawn only when the map is close enough for them to be read,
// and only for the trees in view: an orchard of thousands would otherwise build
// one label per tree and lock the browser while the user waits for them.
// (A selected tree always shows its label, whatever the zoom.)
const TREE_LABEL_MIN_ZOOM = 17;
const TREE_LABEL_MAX_VISIBLE = 1200;

// Label categories that can be toggled from the map widget.
const LABEL_CATEGORIES = [
    { type: 'sector', letter: 'S', label: 'Sector / القطاع' },
    { type: 'zone', letter: 'Z', label: 'Zone / المنطقة' },
    { type: 'tree_row', letter: 'R', label: 'Row / الصف' },
    { type: 'tree', letter: 'T', label: 'Tree / الشجرة' }
];
const labelState = { sector: true, zone: true, tree_row: true, tree: true };

// ---------- Center labels for sector/zone/row/tree features ----------
function escapeLabelHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Rows/trees show a local code (R1, R1T5); sectors/zones keep their full code (S1, S1Z1).
const LOCAL_LABEL_TYPES = ['tree_row', 'tree'];
const SECTOR_ZONE_PREFIX = /^S\d+Z\d+/;

function labelCode(properties) {
    if (!properties) return '';
    const shortName = properties.short_name || '';
    if (!shortName) return '';
    if (LOCAL_LABEL_TYPES.indexOf(properties.type) !== -1) {
        return shortName.replace(SECTOR_ZONE_PREFIX, '') || shortName;
    }
    return shortName;
}

function featureLabelHtml(properties) {
    const code = labelCode(properties);
    const name = properties.name || '';
    const nameAr = properties.name_ar || '';
    const text = code || name;
    if (!text && !nameAr) return null;
    let html = `<span class="map-label-en">${escapeLabelHtml(text || nameAr)}</span>`;
    if (!code && nameAr && nameAr !== text) {
        html += `<span class="map-label-ar">${escapeLabelHtml(nameAr)}</span>`;
    }
    return html;
}

function layerCenterLatLng(layer) {
    if (layer.getBounds) {
        const bounds = layer.getBounds();
        if (bounds && bounds.isValid()) return bounds.getCenter();
    }
    if (layer.getLatLng) return layer.getLatLng();
    return null;
}

// One label marker for a feature, or null when the feature has no code/name.
// The label card centres itself with CSS (translate -50%), so adding hundreds of
// labels costs no layout measurement per label.
function buildFeatureLabel(layer, properties) {
    const html = featureLabelHtml(properties);
    if (!html) return null;
    const center = layerCenterLatLng(layer);
    if (!center) return null;
    return L.marker(center, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
            className: 'map-feature-label-anchor',
            html: '<div class="map-feature-label">' + html + '</div>',
            iconSize: [0, 0]
        })
    });
}

// Every drawn label, by "type:id", so a selection can highlight its own label.
const featureLabelMarkers = new Map();
// Labels drawn only because their feature is selected (a tree below the label
// zoom, or a label the current filter hides).
const selectionLabels = new Map();

function featureKeyOf(properties) {
    if (!properties || properties.id === undefined) return null;
    return properties.type + ':' + properties.id;
}

function addFeatureLabel(layer, properties, group) {
    if (!group) return null;
    const marker = buildFeatureLabel(layer, properties);
    if (marker) {
        marker.addTo(group);
        const key = featureKeyOf(properties);
        if (key) featureLabelMarkers.set(key, marker);
    }
    return marker;
}

// A selected tree/row/zone shows its code even when labels are off at this zoom,
// and the label is highlighted so the picked mark is obvious on the map.
function applySelectionFeedback(layer, properties, selected) {
    if (!map) return;
    const key = featureKeyOf(properties);
    if (!key) return;
    const marker = featureLabelMarkers.get(key);
    let labelElement = marker && marker.getElement ? marker.getElement() : null;
    let card = labelElement && labelElement.querySelector
        ? labelElement.querySelector('.map-feature-label')
        : null;

    if (selected) {
        if (!card) {
            const extra = buildFeatureLabel(layer, properties);
            if (extra) {
                extra.addTo(map);
                extra.setZIndexOffset(2000);
                selectionLabels.set(key, extra);
                labelElement = extra.getElement();
                card = labelElement && labelElement.querySelector
                    ? labelElement.querySelector('.map-feature-label')
                    : null;
            }
        } else {
            marker.setZIndexOffset(2000);
        }
        if (card) card.classList.add('is-selected');
        const icon = layer.getElement ? layer.getElement() : null;
        if (icon) icon.classList.add('is-selected');
        return;
    }

    const extra = selectionLabels.get(key);
    if (extra) {
        if (map.hasLayer(extra)) map.removeLayer(extra);
        selectionLabels.delete(key);
    }
    if (card) card.classList.remove('is-selected');
    if (marker) marker.setZIndexOffset(0);
    const icon = layer.getElement ? layer.getElement() : null;
    if (icon) icon.classList.remove('is-selected');
}

// Trees are labelled on demand: only the trees inside the current view get a
// label, and the labels are dropped again when the view or the zoom moves away.
// ``treeLabelSources`` holds every loaded tree that can be labelled;
// ``treeLabelMarkers`` holds the labels currently drawn.
const treeLabelSources = new Map();
const treeLabelMarkers = new Map();

function treeLabelsVisible() {
    return !!(labelState.tree
        && filterState.tree !== false
        && map && map.getZoom() >= TREE_LABEL_MIN_ZOOM);
}

function syncTreeLabels() {
    if (!map || !labelLayers || !labelLayers.tree) return;
    const group = labelLayers.tree;

    if (!treeLabelsVisible()) {
        if (map.hasLayer(group)) map.removeLayer(group);
        if (treeLabelMarkers.size) {
            treeLabelMarkers.forEach(function(marker, key) {
                if (featureLabelMarkers.get(key) === marker) featureLabelMarkers.delete(key);
            });
            group.clearLayers();
            treeLabelMarkers.clear();
        }
        return;
    }
    if (!map.hasLayer(group)) group.addTo(map);

    // Forget the labels that scrolled out of view.
    const bounds = map.getBounds().pad(0.05);
    Array.from(treeLabelMarkers.keys()).forEach(function(key) {
        const marker = treeLabelMarkers.get(key);
        if (marker && bounds.contains(marker.getLatLng())) return;
        if (marker) group.removeLayer(marker);
        if (featureLabelMarkers.get(key) === marker) featureLabelMarkers.delete(key);
        treeLabelMarkers.delete(key);
    });

    // Label the trees that just came into view, up to a readable density.
    if (treeLabelMarkers.size >= TREE_LABEL_MAX_VISIBLE) return;
    treeLabelSources.forEach(function(entry, key) {
        if (treeLabelMarkers.size >= TREE_LABEL_MAX_VISIBLE) return;
        if (treeLabelMarkers.has(key)) return;
        const layer = entry.layer;
        if (!map.hasLayer(layer) || typeof layer.getLatLng !== 'function') return;
        if (!bounds.contains(layer.getLatLng())) return;
        const marker = buildFeatureLabel(layer, entry.properties);
        if (!marker) return;
        // The regular label replaces a label drawn only for the selection.
        const extra = selectionLabels.get(key);
        if (extra) {
            if (map.hasLayer(extra)) map.removeLayer(extra);
            selectionLabels.delete(key);
        }
        group.addLayer(marker);
        treeLabelMarkers.set(key, marker);
        featureLabelMarkers.set(key, marker);
        if (layer._geometrySelected) {
            const element = marker.getElement();
            const card = element && element.querySelector
                ? element.querySelector('.map-feature-label')
                : null;
            if (card) card.classList.add('is-selected');
        }
    });
}

// Show/hide one category's labels (trees additionally require the zoom threshold).
function applyLabelVisibility(type) {
    if (!map || !labelLayers || !labelLayers[type]) return;
    if (type === 'tree') {
        syncTreeLabels();
        return;
    }
    const group = labelLayers[type];
    if (labelState[type]) {
        if (!map.hasLayer(group)) group.addTo(map);
    } else if (map.hasLayer(group)) {
        map.removeLayer(group);
    }
}

// Map widget with one circle button per label category (Sector/Zone/Row/Tree).
function setupLabelToggleWidget(targetMap, labelTypes) {
    if (!labelLayers) return;
    const categories = LABEL_CATEGORIES.filter(function(category) {
        return labelTypes.indexOf(category.type) !== -1;
    });
    if (!categories.length) return;

    const control = L.control({ position: 'bottomright' });
    control.onAdd = function() {
        const group = L.DomUtil.create('div', 'leaflet-bar map-label-toggle');
        L.DomEvent.disableClickPropagation(group);
        L.DomEvent.disableScrollPropagation(group);

        categories.forEach(function(category) {
            const button = L.DomUtil.create('button', 'geometry-map-select-button map-label-toggle-button', group);
            button.type = 'button';
            button.textContent = category.letter;
            button.title = (labelState[category.type] ? 'Hide' : 'Show') + ' ' + category.label;
            button.setAttribute('aria-label', 'Toggle ' + category.label + ' labels');
            button.setAttribute('aria-pressed', String(!!labelState[category.type]));
            button.classList.toggle('active', !!labelState[category.type]);
            L.DomEvent.on(button, 'click', function(event) {
                L.DomEvent.stop(event);
                labelState[category.type] = !labelState[category.type];
                button.classList.toggle('active', labelState[category.type]);
                button.setAttribute('aria-pressed', String(labelState[category.type]));
                button.title = (labelState[category.type] ? 'Hide ' : 'Show ') + category.label;
                applyLabelVisibility(category.type);
            });
        });

        return group;
    };
    control.addTo(targetMap);
}

// ---------- Optional geometry filter widget ----------
// Pages that pass `mapFilters` get a funnel widget that hides whole element
// categories (geometry and fittings) without reloading the map. Every element
// type the GeoJSON endpoint can emit has a category here, so a page that lists
// them all can filter the whole project.
const FILTER_CATEGORIES = [
    { key: 'sector', letter: 'S', label: 'Sectors / القطاعات', types: ['sector'] },
    { key: 'zone', letter: 'Z', label: 'Zones / المناطق', types: ['zone'] },
    { key: 'tree_row', letter: 'R', label: 'Rows / الصفوف', types: ['tree_row'] },
    { key: 'tree', letter: 'T', label: 'Trees / الأشجار', types: ['tree'] },
    { key: 'pipe', letter: 'P', label: 'Pipes / الأنابيب', types: ['main_pipe', 'second_pipe', 'sub_pipe', 'dripline'] },
    { key: 'master_valve', letter: 'VM', label: 'Main Valves / الصمامات الرئيسية', types: ['master_valve'] },
    { key: 'sector_valve', letter: 'VS', label: 'Sector Valves / صمامات القطاعات', types: ['sector_valve'] },
    { key: 'zone_valve', letter: 'VZ', label: 'Zone Valves / صمامات المناطق', types: ['zone_valve'] },
    { key: 'check_valve', letter: 'VC', label: 'Check Valves / صمامات عدم الرجوع', types: ['check_valve'] },
    { key: 'connector', letter: 'CN', label: 'Connectors / الوصلات', types: ['connector'] },
    { key: 'reduction', letter: 'RD', label: 'Reductors / المخفضات', types: ['reduction'] },
    { key: 'air_release', letter: 'AR', label: 'Air Removal / إزالة الهواء', types: ['air_release'] },
    { key: 'pressure_reduction', letter: 'PR', label: 'Reduce Pressure / خفض الضغط', types: ['pressure_reduction'] },
    { key: 'terminator', letter: 'TM', label: 'Terminators / النهايات', types: ['terminator'] },
    { key: 'boundary', letter: 'B', label: 'Land Boundary / حدود الأرض', types: ['boundary'] },
    { key: 'water_source', letter: 'W', label: 'Water Source / مصدر المياه', types: ['water_source'] }
];
// Every category starts visible; a page may ask for a shorter starting set.
const filterState = {};
FILTER_CATEGORIES.forEach(function(category) { filterState[category.key] = true; });

// ---------- On-demand geometry loading ----------
// A page can pass `lazyFilters: true`: the first request asks only for the
// categories listed in `mapFiltersOn`, and each category is fetched the first
// time its filter button is switched on.  A 20k-tree orchard then opens on the
// sector outlines instead of building twenty thousand Leaflet markers.
function filterCategoryFor(key) {
    return FILTER_CATEGORIES.find(function(item) { return item.key === key; }) || null;
}

function typesForFilterKeys(keys) {
    const types = [];
    (keys || []).forEach(function(key) {
        const category = filterCategoryFor(key);
        if (!category) return;
        category.types.forEach(function(type) {
            if (types.indexOf(type) === -1) types.push(type);
        });
    });
    return types;
}

function geometryUrlWithTypes(types) {
    if (!currentGeojsonUrl) return currentGeojsonUrl;
    const params = [];
    if (types && types.length) params.push('types=' + encodeURIComponent(types.join(',')));
    if (sectorScope !== null) params.push('sector=' + encodeURIComponent(sectorScope));
    if (!params.length) return currentGeojsonUrl;
    const separator = currentGeojsonUrl.indexOf('?') === -1 ? '?' : '&';
    return currentGeojsonUrl + separator + params.join('&');
}

// Add a fetched FeatureCollection to the visible map the same way the initial
// load does (labels, filters, popups are all wired by `featureLayer`).
function addGeometryFeatures(data) {
    if (!data) return;
    data = filterHiddenFeatures(data);
    featureLayer.addData(data);
    (data.features || []).forEach(function(feature) {
        loadedFeatureTypes.add((feature.properties || {}).type || '');
    });
    applyAllGeometryFilters();
    applyAllComponentFilters(currentComponentFilters);
    styleSectorScope();
    syncTreeLabels();
}

function categoryIsLoaded(category) {
    if (!category) return true;
    return category.types.every(function(type) { return loadedFeatureTypes.has(type); });
}

// Fetch the missing feature types of one filter category.  Requests are queued
// instead of dropped, so two quick clicks on different buttons both load.
function loadFilterCategory(category, button) {
    if (!category || categoryIsLoaded(category)) return Promise.resolve();
    const previous = lazyGeometryLoading || Promise.resolve();
    if (button) {
        button.classList.add('map-filter-loading');
        button.setAttribute('aria-busy', 'true');
    }
    const task = previous.catch(function() {}).then(function() {
        if (categoryIsLoaded(category)) return null;
        const missing = category.types.filter(function(type) {
            return !loadedFeatureTypes.has(type);
        });
        return fetch(geometryUrlWithTypes(missing))
            .then(function(response) { return response.json(); })
            .then(function(data) { addGeometryFeatures(data); })
            .catch(function(error) {
                console.error('Error loading geometry for', category.key, error);
            });
    }).then(function() {
        if (button) {
            button.classList.remove('map-filter-loading');
            button.removeAttribute('aria-busy');
        }
        if (lazyGeometryLoading === task) lazyGeometryLoading = null;
    });
    lazyGeometryLoading = task;
    return task;
}

// Which filter category a feature belongs to (null = never filtered).
function featureFilterKey(properties) {
    const type = (properties && properties.type) || '';
    if (!type) return null;
    const category = FILTER_CATEGORIES.find(function(item) {
        return item.types.indexOf(type) !== -1;
    });
    return category ? category.key : null;
}

// Add/remove every feature of one category from the map.
function applyGeometryFilter(key, show) {
    if (!map || !featureLayer) return;
    featureLayer.eachLayer(function(layer) {
        if (!layer.feature || featureFilterKey(layer.feature.properties) !== key) return;
        if (show) {
            if (!map.hasLayer(layer)) layer.addTo(map);
        } else if (map.hasLayer(layer)) {
            if (layer.isPopupOpen && layer.isPopupOpen()) map.closePopup();
            map.removeLayer(layer);
        }
    });
    // Keep the matching labels in sync with their geometry.
    if (labelLayers && labelLayers[key]) {
        if (!show) {
            if (map.hasLayer(labelLayers[key])) map.removeLayer(labelLayers[key]);
        } else {
            applyLabelVisibility(key);
        }
    }
}

function applyAllGeometryFilters() {
    // Fast path: pages without the widget (or with every filter on) keep all layers.
    if (FILTER_CATEGORIES.every(function(category) { return filterState[category.key]; })) return;
    FILTER_CATEGORIES.forEach(function(category) {
        applyGeometryFilter(category.key, filterState[category.key]);
    });
}

// ---------- Optional component (valve / fitting) filter ----------
// A page can pass `componentFilters` to show or hide one kind of fitting
// (valves here, later connectors, reductors, ...). Both filter groups share one
// left-hand widget: two stacked Leaflet controls overlap on a short map, and a
// second set of buttons on the right only repeated the first.
// The page is told about every change through `onComponentFilterChange` so it can
// filter its table the same way.
let componentFilterListener = null;
const componentFilterState = {};

function applyComponentFilter(category, show) {
    if (!map || !featureLayer) return;
    featureLayer.eachLayer(function(layer) {
        if (!layer.feature) return;
        const type = (layer.feature.properties || {}).type || '';
        if (category.types.indexOf(type) === -1) return;
        if (show) {
            if (!map.hasLayer(layer)) layer.addTo(map);
        } else if (map.hasLayer(layer)) {
            if (layer.isPopupOpen && layer.isPopupOpen()) map.closePopup();
            map.removeLayer(layer);
        }
    });
    if (typeof componentFilterListener === 'function') {
        componentFilterListener(category.key, show);
    }
}

function applyAllComponentFilters(categories) {
    categories.forEach(function(category) {
        if (componentFilterState[category.key]) return;  // everything visible by default
        applyComponentFilter(category, false);
    });
}

// Drop the feature types this page asked to never see.
function filterHiddenFeatures(data) {
    if (!hiddenFeatureTypes.length || !data || !Array.isArray(data.features)) {
        return data;
    }
    return Object.assign({}, data, {
        features: data.features.filter(function(feature) {
            const type = ((feature.properties || {}).type) || '';
            return hiddenFeatureTypes.indexOf(type) === -1;
        })
    });
}

// One left-hand widget (under the zoom buttons): fitting kinds first, a divider,
// then the geometry categories. Either group may be empty.
function setupMapFilterWidget(targetMap, filterKeys, componentCategories, listener, enabledKeys) {
    const geometryCategories = FILTER_CATEGORIES.filter(function(category) {
        return (filterKeys || []).indexOf(category.key) !== -1;
    });
    const fittingCategories = componentCategories || [];
    if (!geometryCategories.length && !fittingCategories.length) return;

    componentFilterListener = listener || null;
    fittingCategories.forEach(function(category) {
        if (componentFilterState[category.key] === undefined) componentFilterState[category.key] = true;
    });

    // A page may list the categories it wants switched on at load; the rest of the
    // widget's categories start hidden until the user presses them.
    const startingKeys = Array.isArray(enabledKeys) ? enabledKeys : null;
    initialFilterKeys = startingKeys ? startingKeys.slice() : null;
    if (startingKeys) {
        geometryCategories.forEach(function(category) {
            filterState[category.key] = startingKeys.indexOf(category.key) !== -1;
        });
        fittingCategories.forEach(function(category) {
            componentFilterState[category.key] = startingKeys.indexOf(category.key) !== -1;
        });
    }

    const control = L.control({ position: 'topleft' });
    control.onAdd = function() {
        const group = L.DomUtil.create('div', 'leaflet-bar map-filter-widget');
        L.DomEvent.disableClickPropagation(group);
        L.DomEvent.disableScrollPropagation(group);

        const head = L.DomUtil.create('div', 'map-filter-head', group);
        head.innerHTML = '<i class="bi bi-funnel"></i>';
        head.title = 'Map filters / عوامل تصفية الخريطة';

        fittingCategories.forEach(function(category) {
            const button = L.DomUtil.create('button', 'geometry-map-select-button map-filter-button', group);
            const visible = componentFilterState[category.key] !== false;
            button.type = 'button';
            button.textContent = category.letter;
            button.title = (visible ? 'Hide ' : 'Show ') + category.label;
            button.setAttribute('aria-label', 'Toggle ' + category.label);
            button.setAttribute('aria-pressed', String(visible));
            button.dataset.filterKey = category.key;
            button.classList.toggle('active', visible);
            L.DomEvent.on(button, 'click', function(event) {
                L.DomEvent.stop(event);
                const visible = !componentFilterState[category.key];
                componentFilterState[category.key] = visible;
                button.classList.toggle('active', visible);
                button.setAttribute('aria-pressed', String(visible));
                button.title = (visible ? 'Hide ' : 'Show ') + category.label;
                if (visible && lazyGeometry) {
                    loadFilterCategory(category, button).then(function() {
                        // The user may have switched it off again while loading.
                        applyComponentFilter(category, componentFilterState[category.key] !== false);
                    });
                } else {
                    applyComponentFilter(category, visible);
                }
            });
        });

        if (fittingCategories.length && geometryCategories.length) {
            L.DomUtil.create('div', 'map-filter-divider', group);
        }

        geometryCategories.forEach(function(category) {
            const button = L.DomUtil.create('button', 'geometry-map-select-button map-filter-button', group);
            const visible = filterState[category.key] !== false;
            button.type = 'button';
            button.textContent = category.letter;
            button.title = (visible ? 'Hide ' : 'Show ') + category.label;
            button.setAttribute('aria-label', 'Toggle ' + category.label);
            button.setAttribute('aria-pressed', String(visible));
            button.dataset.filterKey = category.key;
            button.classList.toggle('active', visible);
            L.DomEvent.on(button, 'click', function(event) {
                L.DomEvent.stop(event);
                filterState[category.key] = !filterState[category.key];
                const visible = filterState[category.key];
                button.classList.toggle('active', visible);
                button.setAttribute('aria-pressed', String(visible));
                button.title = (visible ? 'Hide ' : 'Show ') + category.label;
                if (visible && lazyGeometry) {
                    loadFilterCategory(category, button).then(function() {
                        // The user may have switched it off again while loading.
                        applyGeometryFilter(category.key, filterState[category.key] !== false);
                    });
                } else {
                    applyGeometryFilter(category.key, visible);
                }
            });
        });

        return group;
    };
    control.addTo(targetMap);

    applyAllGeometryFilters();
    applyAllComponentFilters(fittingCategories);
}

// Reload the current geometry without a page navigation (rows/orientation edits).
window.reloadMapGeometry = function() {
    if (!currentGeojsonUrl || !featureLayer) return Promise.resolve(false);
    // Keep the on-demand contract: refresh only the categories already loaded.
    const types = lazyGeometry ? Array.from(loadedFeatureTypes) : [];
    // Nothing is loaded yet: refetching with an empty list would draw the whole
    // project and defeat the on-demand contract.
    if (lazyGeometry && !types.length) return Promise.resolve(true);
    return fetch(geometryUrlWithTypes(types))
        .then(function(response) { return response.json(); })
        .then(function(data) {
            clearGeometryLayers();
            addGeometryFeatures(data);
            return true;
        })
        .catch(function(err) {
            console.error('Error reloading geometry:', err);
            return false;
        });
};

// ---------- Sector scope ----------
// Selecting a sector is a layout view, not a "show me everything" view: only
// the sector and zone filters come on (blueprint of the chosen sector plus the
// zones inside it), and the other sectors become dashed boundaries for
// context.  Every other category stays off until the user asks for it.
// The water sources stay on in a scoped view: the well or basin feeds every
// sector, so it is context for the layout rather than part of it.
const SECTOR_SCOPE_FILTER_KEYS = ['sector', 'zone', 'water_source'];

// Initial shown inside a water source marker, one per kind the API emits.
const WATER_SOURCE_LETTERS = {
    well: 'W', basin: 'B', reservoir: 'R', pump: 'P', canal: 'C', other: 'S'
};

function setFilterStateFromKeys(keys) {
    const list = Array.isArray(keys) ? keys : null;
    FILTER_CATEGORIES.forEach(function(category) {
        filterState[category.key] = list ? list.indexOf(category.key) !== -1 : true;
    });
}

// Repaint the funnel widget from the current state (scope changes move it).
function syncFilterButtons() {
    const buttons = document.querySelectorAll('.map-filter-button[data-filter-key]');
    Array.prototype.forEach.call(buttons, function(button) {
        const key = button.dataset.filterKey;
        // Fitting kinds own their buttons even when a geometry category shares
        // the key (connector, reduction, air_release, ...).
        const ownedByFitting = (currentComponentFilters || [])
            .some(function(item) { return item.key === key; });
        const state = ownedByFitting ? componentFilterState[key] : filterState[key];
        const visible = state !== false;
        button.classList.toggle('active', visible);
        button.setAttribute('aria-pressed', String(visible));
        const category = filterCategoryFor(key) ||
            (currentComponentFilters || []).find(function(item) { return item.key === key; });
        if (category) button.title = (visible ? 'Hide ' : 'Show ') + category.label;
    });
}

// The scoped sector keeps its fill; the others are outlines only.
function styleSectorScope() {
    if (!featureLayer) return;
    featureLayer.eachLayer(function(layer) {
        const properties = (layer.feature && layer.feature.properties) || {};
        if (properties.type !== 'sector' || !layer.setStyle) return;
        if (layer._geometrySelected) return;   // the selection colour wins
        if (sectorScope === null) {
            layer.setStyle({ color: properties.color || '#3388ff', weight: 2,
                             fill: true, fillOpacity: 0.2, dashArray: null });
        } else if (String(properties.id) === String(sectorScope)) {
            layer.setStyle({ color: '#0d6efd', weight: 3,
                             fill: true, fillOpacity: 0.12, dashArray: null });
        } else {
            layer.setStyle({ color: '#868e96', weight: 1.5,
                             fill: false, dashArray: '5 4' });
        }
    });
}

// Types to ask for: the categories currently switched on.  Reading the live
// filter state (not `initialFilterKeys`) matters twice: a scoped sector only
// asks for its layout (sectors + zones), and leaving a sector has to bring back
// the categories the page was showing before the scope.
function typesForCurrentState() {
    // Pages without on-demand loading (the card maps) ask for everything.
    if (!lazyGeometry) return [];
    const keys = FILTER_CATEGORIES
        .filter(function(category) { return filterState[category.key]; })
        .map(function(category) { return category.key; });
    return typesForFilterKeys(keys);
}

// Drop every drawn feature and its labels, keeping the GeoJSON layer itself.
function clearGeometryLayers() {
    if (labelLayers) {
        Object.keys(labelLayers).forEach(function(type) {
            labelLayers[type].clearLayers();
        });
    }
    treeLabelSources.clear();
    treeLabelMarkers.clear();
    featureLabelMarkers.clear();
    if (map) {
        selectionLabels.forEach(function(marker) {
            if (map.hasLayer(marker)) map.removeLayer(marker);
        });
    }
    selectionLabels.clear();
    if (featureLayer) featureLayer.clearLayers();
    loadedFeatureTypes.clear();
}

function refetchScopedGeometry() {
    if (!currentGeojsonUrl || !featureLayer) return Promise.resolve(false);
    const previous = lazyGeometryLoading || Promise.resolve();
    const task = previous.catch(function() {}).then(function() {
        const types = typesForCurrentState();
        // On a lazy page every category may be switched off: an empty `types`
        // list would fetch the whole project, so draw nothing instead.
        if (lazyGeometry && !types.length) {
            clearGeometryLayers();
            document.dispatchEvent(new CustomEvent('mapgeometryloaded'));
            return true;
        }
        return fetch(geometryUrlWithTypes(types))
            .then(function(response) { return response.json(); })
            .then(function(data) {
                clearGeometryLayers();
                addGeometryFeatures(data);
                styleSectorScope();
                if (sectorScope !== null) zoomToFeature('sector', sectorScope);
                else fitAllFeatures();
                document.dispatchEvent(new CustomEvent('mapgeometryloaded'));
                return true;
            });
    }).catch(function(error) {
        console.error('Error loading sector geometry:', error);
        return false;
    });
    lazyGeometryLoading = task;
    task.then(function() {
        if (lazyGeometryLoading === task) lazyGeometryLoading = null;
    });
    return task;
}

// Zoom the map to one sector and show its layout (sector + zones); null goes
// back to the whole project with the page's starting categories.
window.setSectorScope = function(sectorId) {
    const next = (sectorId === null || sectorId === undefined || sectorId === '')
        ? null : String(sectorId);
    if (next === sectorScope) return Promise.resolve(false);
    if (next !== null && sectorScopeFilterState === null) {
        // Remember the buttons the page was showing, to restore them on "All".
        sectorScopeFilterState = FILTER_CATEGORIES
            .filter(function(category) { return filterState[category.key]; })
            .map(function(category) { return category.key; });
    }
    sectorScope = next;
    if (next === null) {
        setFilterStateFromKeys(sectorScopeFilterState || initialFilterKeys);
        sectorScopeFilterState = null;
    } else {
        // Only the sector layout and its zones, never the whole orchard.
        setFilterStateFromKeys(SECTOR_SCOPE_FILTER_KEYS);
    }
    syncFilterButtons();
    return refetchScopedGeometry();
};

// ---------- Whole-map helpers (Map Overview tab) ----------
// Find one loaded feature by its type and id.
function mapFeatureLayer(type, id) {
    let found = null;
    if (!featureLayer) return null;
    featureLayer.eachLayer(function(layer) {
        if (found || !layer.feature) return;
        const properties = layer.feature.properties || {};
        if (properties.type === type && String(properties.id) === String(id)) found = layer;
    });
    return found;
}

// Zoom to one feature (a sector from the sector widget, ...).
window.zoomToFeature = function(type, id) {
    const layer = mapFeatureLayer(type, id);
    if (!map || !layer) return false;
    if (typeof layer.getBounds === 'function') {
        const bounds = layer.getBounds();
        if (bounds && bounds.isValid()) {
            map.fitBounds(bounds, { padding: [40, 40], maxZoom: 19 });
        }
    } else if (typeof layer.getLatLng === 'function') {
        map.setView(layer.getLatLng(), Math.max(map.getZoom(), 18));
    }
    if (layer.bringToFront) layer.bringToFront();
    return true;
};

// Frame the whole project.
window.fitAllFeatures = function() {
    if (!map || !featureLayer) return false;
    const bounds = featureLayer.getBounds();
    if (!bounds || !bounds.isValid()) return false;
    map.fitBounds(bounds, { padding: [30, 30] });
    return true;
};

// Drop every selection made on the map (keeps the layers in place).
window.clearMapSelection = function() {
    if (!featureLayer) return;
    featureLayer.eachLayer(function(layer) {
        if (!layer._geometrySelected) return;
        const properties = (layer.feature && layer.feature.properties) || {};
        layer._geometrySelected = false;
        if (layer.setStyle) {
            layer.setStyle({ color: properties.color || '#3388ff', weight: 2, fillOpacity: 0.2 });
        }
        applySelectionFeedback(layer, properties, false);
        if (typeof mapSelectionListener === 'function') {
            mapSelectionListener(layer, properties, false);
        }
    });
};

// Registry of Leaflet map instances keyed by their container element id.
const mapRegistry = {};
function registerMap(elementId, instance) {
    if (elementId && instance) {
        mapRegistry[elementId] = instance;
    }
}

// ---------- Geodesic helpers ----------
function ringAreaM2(ring) {
    // ring: [[lng, lat], ...] ; spherical excess approximation (same method as Leaflet.draw)
    if (!ring || ring.length < 3) return 0;
    const d2r = Math.PI / 180;
    let area = 0;
    for (let i = 0; i < ring.length; i++) {
        const p1 = ring[i];
        const p2 = ring[(i + 1) % ring.length];
        area += ((p2[0] - p1[0]) * d2r) * (2 + Math.sin(p1[1] * d2r) + Math.sin(p2[1] * d2r));
    }
    return Math.abs(area * 6378137 * 6378137 / 2);
}

function ringLengthM(ring, closed) {
    let total = 0;
    for (let i = 1; i < ring.length; i++) {
        total += L.latLng(ring[i - 1][1], ring[i - 1][0]).distanceTo(L.latLng(ring[i][1], ring[i][0]));
    }
    if (closed && ring.length > 2) {
        const a = ring[ring.length - 1], b = ring[0];
        if (a[0] !== b[0] || a[1] !== b[1]) {
            total += L.latLng(a[1], a[0]).distanceTo(L.latLng(b[1], b[0]));
        }
    }
    return total;
}

function geometryAreaStats(geometry) {
    // Returns { area, perimeter } in m² / m for Polygon & MultiPolygon, otherwise null
    if (!geometry) return null;
    let polygons;
    if (geometry.type === 'Polygon') polygons = [geometry.coordinates];
    else if (geometry.type === 'MultiPolygon') polygons = geometry.coordinates;
    else return null;

    let area = 0, perimeter = 0;
    polygons.forEach(function(rings) {
        rings.forEach(function(ring, index) {
            const ringArea = ringAreaM2(ring);
            area += index === 0 ? ringArea : -ringArea;   // holes are subtracted
            perimeter += ringLengthM(ring, true);
        });
    });
    return { area: Math.max(area, 0), perimeter: perimeter };
}

function formatAreaM2(m2) {
    const fmt = (value, digits) => value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return fmt(m2, 0) + ' m² · ' + fmt(m2 / 10000, 2) + ' ha';
}

function formatDistanceM(m) {
    if (m >= 1000) return (m / 1000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' km';
    return m.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' m';
}

const BASE_LAYER_STORAGE_KEY = 'fis.baseLayer';

function addBaseLayers(targetMap, options) {
    const esriAttribution = 'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community';
    const esriImagery = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
    const esriLabels = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';

    // Separate tile-layer instances per base layer so the layer control never marks two as active
    const baseLayers = {
        'Street / خريطة': L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        }),
        'Satellite / قمر صناعي': L.tileLayer(esriImagery, {
            attribution: esriAttribution,
            maxNativeZoom: 18,
            maxZoom: 19
        }),
        'Hybrid / هجين': L.layerGroup([
            L.tileLayer(esriImagery, { attribution: esriAttribution, maxNativeZoom: 18, maxZoom: 19 }),
            L.tileLayer(esriLabels, { maxNativeZoom: 18, maxZoom: 19 })
        ]),
        'Terrain / تضاريس': L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)',
            maxNativeZoom: 17,
            maxZoom: 19
        })
    };

    let saved = null;
    try { saved = window.localStorage.getItem(BASE_LAYER_STORAGE_KEY); } catch (e) { /* storage unavailable */ }
    const initialName = saved && baseLayers[saved] ? saved : Object.keys(baseLayers)[0];
    baseLayers[initialName].addTo(targetMap);

    if (options.layerSwitcher === false) return;

    L.control.layers(baseLayers, null, { position: 'topright', collapsed: true }).addTo(targetMap);
    targetMap.on('baselayerchange', function(event) {
        try { window.localStorage.setItem(BASE_LAYER_STORAGE_KEY, event.name); } catch (e) { /* ignore */ }
    });
}

function addNorthIndicator(targetMap) {
    const NorthControl = L.Control.extend({
        options: { position: 'bottomleft' },
        onAdd: function() {
            const div = L.DomUtil.create('div', 'leaflet-north-indicator');
            div.innerHTML = '<span class="north-needle"></span><span class="north-label">N</span>';
            L.DomEvent.disableClickPropagation(div);
            L.DomEvent.disableScrollPropagation(div);
            const needle = div.querySelector('.north-needle');
            const update = function() {
                const bearing = (targetMap.getBearing ? targetMap.getBearing() : 0) || 0;
                needle.style.transform = 'rotate(' + bearing + 'deg)';
            };
            targetMap.on('rotate', update);
            update();
            return div;
        }
    });
    targetMap.addControl(new NorthControl());
}

// options.mapFilters    -> filter categories shown in the funnel widget (keys of
//                          FILTER_CATEGORIES, e.g. 'sector', 'zone', 'check_valve').
// options.mapFiltersOn  -> optional subset that starts switched on; omit it to show
//                          every listed category.
// options.componentFilters -> fitting kinds driven by the page's own table.
function initMap(elementId, geojsonUrl, options = {}) {
    const mapElement = document.getElementById(elementId);
    if (!mapElement) return null;
    currentGeojsonUrl = geojsonUrl || currentGeojsonUrl;
    hiddenFeatureTypes = Array.isArray(options.hiddenTypes) ? options.hiddenTypes : [];
    // Pages that open on a subset (Map Overview: sectors only) fetch the other
    // categories later, when the matching filter button is switched on.
    lazyGeometry = !!(options.lazyFilters && Array.isArray(options.mapFiltersOn));
    loadedFeatureTypes.clear();

    const mapHeight = options.height || '500px';
    mapElement.style.height = mapHeight;

    map = L.map(elementId).setView(options.center || [34.0, -6.0], options.zoom || 13);
    registerMap(elementId, map);

    addBaseLayers(map, options);

    // North indicator (always visible) + scale bar
    addNorthIndicator(map);
    L.control.scale().addTo(map);

    // Drawn items layer
    drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);

    if (options.editor) {
        const drawControl = new L.Control.Draw({
            position: 'topleft',
            draw: {
                polygon: { allowIntersection: false, showArea: true },
                polyline: { allowIntersection: false },
                marker: true,
                rectangle: false,
                circle: false,
                circlemarker: false
            },
            edit: {
                featureGroup: drawnItems,
                edit: false,
                remove: true
            }
        });
        map.addControl(drawControl);
        map.on(L.Draw.Event.CREATED, function(event) {
            drawnItems.addLayer(event.layer);
            if (options.onDrawCreated) {
                options.onDrawCreated(event.layer, event.layerType);
            }
        });

        if (options.ruler) {
            initRulerTool(drawControl);
        }
    }

    // Popups should not compete with the ruler while measuring, nor with
    // sector picking: a popup opened by the first clicked sector covers its
    // neighbours and would swallow the clicks meant to add them to the
    // selection.
    map.on('popupopen', function() {
        if (mapRulerActive) map.closePopup();
        else if (sectorSelection && sectorSelection.pickMode) map.closePopup();
    });

    mapSelectionMode = false;
    mapSelectionListener = (options.selectable && typeof options.selectable.onSelectionChange === 'function')
        ? options.selectable.onSelectionChange
        : null;
    const selectableTypes = options.selectable
        ? (Array.isArray(options.selectable.type) ? options.selectable.type : [options.selectable.type])
        : [];
    const labelTypes = options.labelTypes || ['sector', 'zone'];

    // Permanent labels (sector S1, zone S1Z1, row R1, tree R1T5), one layer per
    // category so the map widget can show/hide each of them. Created before the
    // GeoJSON layer so onEachFeature can route labels into the right group.
    labelLayers = {};
    LABEL_CATEGORIES.forEach(function(category) {
        const group = L.layerGroup();
        labelLayers[category.type] = group;
        if (labelTypes.indexOf(category.type) !== -1 && category.type !== 'tree') {
            group.addTo(map);
        }
    });

    // Tree labels follow the zoom and the view, so only readable, on-screen
    // trees carry one.
    map.on('zoomend moveend', syncTreeLabels);

    // Optional left-hand filter widget (valve kinds and/or geometry categories).
    const geometryFilters = Array.isArray(options.mapFilters) ? options.mapFilters : [];
    const componentFilters = Array.isArray(options.componentFilters) ? options.componentFilters : [];
    currentComponentFilters = componentFilters;
    if (geometryFilters.length || componentFilters.length) {
        setupMapFilterWidget(map, geometryFilters, componentFilters,
                              options.onComponentFilterChange, options.mapFiltersOn);
    }

    // The label widget would repeat the geometry buttons, so a page that filters
    // geometry on the left gets no second set on the right.
    if (!geometryFilters.length) {
        setupLabelToggleWidget(map, labelTypes);
    }

    if (options.selectable) {
        const selectionControl = L.control({ position: 'topright' });
        selectionControl.onAdd = function() {
            const container = L.DomUtil.create('div', 'leaflet-bar geometry-map-select');
            const button = L.DomUtil.create('button', 'geometry-map-select-button', container);
            button.type = 'button';
            button.title = 'Select elements on map';
            button.setAttribute('aria-label', 'Select elements on map');
            button.setAttribute('aria-pressed', 'false');
            button.innerHTML = '<i class="bi bi-cursor"></i>';
            L.DomEvent.disableClickPropagation(container);
            L.DomEvent.on(button, 'click', function(event) {
                L.DomEvent.stop(event);
                mapSelectionMode = !mapSelectionMode;
                button.classList.toggle('active', mapSelectionMode);
                button.setAttribute('aria-pressed', String(mapSelectionMode));
                map.getContainer().classList.toggle('geometry-map-select-mode', mapSelectionMode);
            });
            return container;
        };
        selectionControl.addTo(map);

        // Action buttons (Split / Merge / Swap) live in their own group below Select
        const actions = buildMapActionButtons(options);
        if (actions.length) {
            const actionsControl = L.control({ position: 'topright' });
            actionsControl.onAdd = function() {
                const group = L.DomUtil.create('div', 'leaflet-bar geometry-map-select geometry-map-actions');
                L.DomEvent.disableClickPropagation(group);
                actions.forEach(function(action) {
                    const actionButton = L.DomUtil.create('button', 'geometry-map-select-button geometry-map-action-button', group);
                    actionButton.type = 'button';
                    actionButton.title = action.title;
                    actionButton.setAttribute('aria-label', action.title);
                    actionButton.innerHTML = '<i class="bi ' + action.icon + '"></i>';
                    if (action.role === 'delete') {
                        actionButton.classList.add('geometry-map-delete-button');
                        actionButton.disabled = true;
                        wireMapDeleteGating(actionButton, options.selectable && options.selectable.checkboxSelector);
                    }
                    L.DomEvent.on(actionButton, 'click', function(event) {
                        L.DomEvent.stop(event);
                        if (actionButton.disabled) return;
                        action.onClick();
                    });
                });
                return group;
            };
            actionsControl.addTo(map);
            createMapActionPanel(options);
        }
    }

    // Optional single/multi sector selection with a left-side action widget.
    sectorSelection = null;
    if (options.sectorSelect && options.sectorSelect.enabled !== false) {
        setupSectorSelection(map, options.sectorSelect);
    }

    // Feature layer for loaded data
    featureLayer = L.geoJSON(null, {
        style: function(feature) {
            const color = feature.properties?.color || '#3388ff';
            return { color: color, weight: 2, fillOpacity: 0.2 };
        },
        pointToLayer: function(feature, latlng) {
            const color = feature.properties?.color || '#3388ff';
            const type = feature.properties?.type || '';
            let icon;
            if (type === 'tree') {
                icon = L.divIcon({
                    className: 'tree-marker',
                    html: `<div style="background:${color};width:8px;height:8px;border-radius:50%;border:1px solid white"></div>`,
                    iconSize: [10, 10]
                });
            } else if (type === 'water_source') {
                // Wells, basins and the other sources are project landmarks, so
                // each one carries its kind's initial instead of a generic dot:
                // an unlabelled cyan dot read as "the basin is not on the map".
                const kind = String(feature.properties?.source_type || '').toLowerCase();
                const letter = WATER_SOURCE_LETTERS[kind] || 'S';
                icon = L.divIcon({
                    className: 'water-source-marker',
                    html: `<div style="background:${color};width:22px;height:22px;border-radius:6px;border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.4);color:#fff;font:700 12px/18px system-ui,sans-serif;text-align:center;text-shadow:0 1px 2px rgba(0,0,0,.45)">${letter}</div>`,
                    iconSize: [26, 26],
                    iconAnchor: [13, 13]
                });
            } else if (type.includes('valve') || type.includes('pipe') || type === 'dripline' || type.includes('air') || type.includes('pressure') || type === 'reduction' || type === 'other') {
                icon = L.divIcon({
                    className: 'network-marker',
                    html: `<div style="background:${color};width:14px;height:14px;border-radius:3px;border:2px solid white"></div>`,
                    iconSize: [18, 18]
                });
            } else {
                icon = L.divIcon({
                    className: 'point-marker',
                    html: `<div style="background:${color};width:12px;height:12px;border-radius:50%;border:2px solid white"></div>`,
                    iconSize: [16, 16]
                });
            }
            return L.marker(latlng, { icon: icon });
        },
        onEachFeature: function(feature, layer) {
            if (feature.properties) {
                const properties = feature.properties;
                const name = feature.properties.name || '';
                const nameAr = feature.properties.name_ar || '';
                const type = feature.properties.type || '';
                const validated = feature.properties.validated;
                // The popup HTML is built on click instead of for every feature:
                // a large orchard has tens of thousands of them and the strings
                // (plus the area maths) are only ever read one at a time.
                layer.bindPopup(function() {
                    let popupContent = `<b>${escapeLabelHtml(labelCode(properties) || name)}</b>`;
                    if (name && labelCode(properties)) popupContent += ` &ndash; ${escapeLabelHtml(name)}`;
                    if (nameAr) popupContent += ` / <span class="ar-text">${nameAr}</span>`;
                    // Water sources carry the kind of source (well, basin, ...).
                    popupContent += `<br><small>Type: ${escapeLabelHtml(properties.source_type || type)}</small>`;
                    if (properties.flow_lpm !== undefined && properties.flow_lpm !== null) {
                        popupContent += `<br><small>Flow / التدفق: ${escapeLabelHtml(properties.flow_lpm)} L/min</small>`;
                    }
                    if (validated !== undefined) {
                        popupContent += `<br><small>Status: ${validated ? '✓ Validated' : 'Pending'}</small>`;
                    }
                    if (type === 'zone' && properties.orientation_deg !== undefined) {
                        const deg = properties.orientation_deg;
                        const compass = properties.orientation || '';
                        popupContent += `<br><small><b>Orientation / الاتجاه:</b> ${deg}° (${compass})</small>`;
                    }
                    if (options.showArea) {
                        const stats = geometryAreaStats(feature.geometry);
                        if (stats) {
                            popupContent += `<br><small><b>Area / المساحة:</b> ${formatAreaM2(stats.area)}</small>`;
                            popupContent += `<br><small><b>Perimeter / المحيط:</b> ${formatDistanceM(stats.perimeter)}</small>`;
                        }
                    }
                    return popupContent;
                });

                if (options.selectable && selectableTypes.indexOf(type) !== -1 && properties.id !== undefined) {
                    // Only pages wired to a checkbox table have a selector; without
                    // one this lookup would run (and find nothing) per feature,
                    // which is costly on a large orchard.
                    const checkboxSelector = options.selectable.checkboxSelector;
                    const checkbox = checkboxSelector
                        ? Array.from(document.querySelectorAll(checkboxSelector))
                            .find((input) => input.value === String(properties.id))
                        : null;
                    const setSelectedStyle = (selected) => {
                        layer._geometrySelected = selected;
                        if (layer.setStyle) {
                            layer.setStyle({
                                color: selected ? '#f08c00' : properties.color || '#3388ff',
                                weight: selected ? 4 : 2,
                                fillOpacity: selected ? 0.4 : 0.2
                            });
                        }
                        applySelectionFeedback(layer, properties, selected);
                        if (typeof mapSelectionListener === 'function') {
                            mapSelectionListener(layer, properties, selected);
                        }
                    };

                    if (checkbox) {
                        setSelectedStyle(checkbox.checked);
                        checkbox.addEventListener('change', () => setSelectedStyle(checkbox.checked));
                    }

                    layer.on('click', function(event) {
                        const alwaysToggle = !!(options.selectable && options.selectable.toggleOnClick);
                        if ((!mapSelectionMode && !alwaysToggle) || mapRulerActive) return;
                        if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
                        if (checkbox) {
                            checkbox.checked = !checkbox.checked;
                            checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                        } else {
                            setSelectedStyle(!layer._geometrySelected);
                        }
                    });
                }

                if (sectorSelection && type === 'sector' && properties.id !== undefined) {
                    registerSectorLayer(layer, properties);
                } else if (sectorSelection && layer.setStyle) {
                    // Any other polygon overlay (zones, boundary, ...) is muted
                    // while the sector pick mode is active.
                    sectorSelection.blockLayers.push(layer);
                }

                if (labelTypes.indexOf(type) !== -1 && labelLayers && labelLayers[type]) {
                    if (type === 'tree') {
                        // Trees are labelled lazily, per view, by syncTreeLabels.
                        treeLabelSources.set(properties.id !== undefined
                            ? type + ':' + properties.id
                            : 'tree:' + treeLabelSources.size, { layer: layer, properties: properties });
                    } else {
                        addFeatureLabel(layer, properties, labelLayers[type]);
                    }
                }
            }
        }
    }).addTo(map);

    // Load GeoJSON data if URL provided.  With lazy geometry only the categories
    // shown at load are requested; the rest arrive when their filter is enabled.
    if (geojsonUrl) {
        const initialTypes = lazyGeometry ? typesForFilterKeys(options.mapFiltersOn) : [];
        fetch(geometryUrlWithTypes(initialTypes))
            .then(response => response.json())
            .then(data => {
                addGeometryFeatures(data);
                if (data.features && data.features.length > 0) {
                    map.fitBounds(featureLayer.getBounds(), { padding: [50, 50] });
                }
                // Pages with a sector list wait for this before zooming.
                document.dispatchEvent(new CustomEvent('mapgeometryloaded'));
            })
            .catch(err => console.error('Error loading geometry:', err));
    }

    return map;
}

// ---------- Map actions: Split / Merge / Swap on the selected features ----------
// options.mapActions = {
//   idField: 'sector_ids',                     // form field used for Split / Merge
//   item: { en: 'sector', ar: 'قطاع' },        // wording used in the panel
//   urls: { split: '...', merge: '...', swap: '...' },   // omit one to hide its button
//   splitOptions: [2, 3, 4]
// }
const MAP_ACTION_DEFS = {
    split: { icon: 'bi-scissors', btn: 'btn-warning', label: 'Split / تقسيم', title: 'Split / تقسيم', min: 1, max: Infinity, parts: true,
             hint: 'Select at least 1 / اختر واحدا على الأقل' },
    merge: { icon: 'bi-union', btn: 'btn-primary', label: 'Merge / دمج', title: 'Merge / دمج', min: 2, max: Infinity, parts: false,
             hint: 'Select at least 2 / اختر اثنين على الأقل' },
    swap:  { icon: 'bi-arrow-left-right', btn: 'btn-primary', label: 'Swap / تبديل', title: 'Swap / تبديل', min: 2, max: 2, parts: false,
             hint: 'Select exactly 2 / اختر اثنين بالضبط' }
};

let mapActionPanel = null;
let mapActionMode = null;
let mapActionConfig = null;

function buildMapActionButtons(options) {
    const buttons = [];
    const config = options.mapActions;
    if (config && config.urls) {
        ['split', 'merge', 'swap']
            .filter(function(mode) { return config.urls[mode]; })
            .forEach(function(mode) {
                const def = MAP_ACTION_DEFS[mode];
                buttons.push({ icon: def.icon, title: def.title, onClick: function() { toggleMapActionPanel(mode); } });
            });
    }
    if (options.mapDelete && typeof options.mapDelete.onClick === 'function') {
        buttons.push({
            icon: 'bi-trash',
            title: options.mapDelete.title || 'Remove / إزالة',
            role: 'delete',
            onClick: options.mapDelete.onClick
        });
    }
    return buttons;
}

// Keeps an in-map delete button disabled until at least one item is selected.
function wireMapDeleteGating(button, selector) {
    if (!selector) { button.disabled = false; return; }
    const update = function() {
        button.disabled = document.querySelectorAll(selector + ':checked').length < 1;
    };
    document.addEventListener('change', function(event) {
        if (event.target && event.target.matches && event.target.matches(selector)) update();
    });
    update();
}

function createMapActionPanel(options) {
    if (!options.mapActions || !options.mapActions.urls) return;
    mapActionConfig = options.mapActions;
    mapActionConfig.checkboxSelector = options.selectable.checkboxSelector;
    const splitOptions = mapActionConfig.splitOptions || [2, 3, 4];

    const control = L.control({ position: 'topright' });
    control.onAdd = function() {
        const panel = L.DomUtil.create('div', 'geometry-map-split-panel');
        panel.hidden = true;
        panel.innerHTML = `
            <div class="fw-bold mb-1" data-role="title"></div>
            <div class="small mb-2" data-role="info"></div>
            <div data-role="parts-wrap">
                <label class="form-label small mb-1">Parts / الأجزاء</label>
                <select class="form-select form-select-sm mb-2" data-role="parts">
                    ${splitOptions.map(n => `<option value="${n}">${n}</option>`).join('')}
                </select>
            </div>
            <div class="d-flex gap-1">
                <button type="button" class="btn btn-sm flex-fill" data-role="apply"></button>
                <button type="button" class="btn btn-secondary btn-sm" data-role="cancel" aria-label="Close">&times;</button>
            </div>`;
        L.DomEvent.disableClickPropagation(panel);
        L.DomEvent.disableScrollPropagation(panel);
        panel.querySelector('[data-role="cancel"]').addEventListener('click', closeMapActionPanel);
        panel.querySelector('[data-role="apply"]').addEventListener('click', submitMapAction);
        mapActionPanel = panel;
        return panel;
    };
    control.addTo(map);
}

function closeMapActionPanel() {
    if (mapActionPanel) mapActionPanel.hidden = true;
    mapActionMode = null;
}

function selectedMapItems() {
    return Array.from(document.querySelectorAll(mapActionConfig.checkboxSelector + ':checked'));
}

function toggleMapActionPanel(mode) {
    if (!mapActionPanel) return;
    if (!mapActionPanel.hidden && mapActionMode === mode) { closeMapActionPanel(); return; }

    const def = MAP_ACTION_DEFS[mode];
    const item = mapActionConfig.item || { en: 'item', ar: 'عنصر' };
    const count = selectedMapItems().length;
    const ok = count >= def.min && count <= def.max;

    mapActionMode = mode;
    mapActionPanel.querySelector('[data-role="title"]').innerHTML = `<i class="bi ${def.icon}"></i> ${def.title}`;
    mapActionPanel.querySelector('[data-role="info"]').textContent =
        ok ? `${count} ${item.en}(s) selected / ${count} ${item.ar} محدد`
           : `${def.hint}. Use the Select button first / استخدم زر التحديد أولا`;
    mapActionPanel.querySelector('[data-role="parts-wrap"]').hidden = !def.parts;
    const apply = mapActionPanel.querySelector('[data-role="apply"]');
    apply.className = `btn btn-sm flex-fill ${def.btn}`;
    apply.textContent = def.label;
    apply.disabled = !ok;
    mapActionPanel.hidden = false;
}

function submitMapAction() {
    const def = MAP_ACTION_DEFS[mapActionMode];
    const selected = selectedMapItems();
    if (!def || selected.length < def.min || selected.length > def.max) return;

    const form = document.createElement('form');
    form.method = 'POST';
    form.action = mapActionConfig.urls[mapActionMode];
    const add = (name, value) => {
        const input = document.createElement('input');
        input.type = 'hidden'; input.name = name; input.value = value;
        form.appendChild(input);
    };
    if (mapActionMode === 'swap') {
        add('id1', selected[0].value);
        add('id2', selected[1].value);
    } else {
        selected.forEach(cb => add(mapActionConfig.idField, cb.value));
    }
    if (def.parts) add('parts', mapActionPanel.querySelector('[data-role="parts"]').value);
    document.body.appendChild(form);
    form.submit();
}

// ---------- Sector selection (select sector by sector) + left-side widget ----------
// options.sectorSelect = {
//   pickTitle: 'Select sector / اختر قطاعا',
//   onSelectionChange: function(ids) {},
//   actions: [ { icon: 'bi-magic', title: '...', enabledWhen: fn(count), onClick: fn(ids) } ]
// }
// Clicking a sector toggles its selection. Zones are made non-interactive while
// pick mode is on so clicks reach the sector polygons underneath.
function setupSectorSelection(targetMap, config) {
    const state = {
        config: config,
        ids: [],
        layers: new Map(),
        blockLayers: [],
        pickMode: false,
        pickButton: null,
        actionButtons: []
    };

    const control = L.control({ position: 'topleft' });
    control.onAdd = function() {
        const group = L.DomUtil.create('div', 'leaflet-bar geometry-sector-widget');
        L.DomEvent.disableClickPropagation(group);

        const pick = L.DomUtil.create('button', 'geometry-map-select-button geometry-sector-pick-button', group);
        pick.type = 'button';
        pick.title = config.pickTitle || 'Select sector / اختر قطاعا';
        pick.setAttribute('aria-label', pick.title);
        pick.setAttribute('aria-pressed', 'false');
        pick.innerHTML = '<i class="bi bi-bounding-box"></i>';
        pick.addEventListener('click', function() { setSectorPickMode(targetMap, state, !state.pickMode); });
        state.pickButton = pick;

        (config.actions || []).forEach(function(action) {
            const button = L.DomUtil.create('button', 'geometry-map-select-button geometry-sector-action-button', group);
            button.type = 'button';
            button.title = action.title || '';
            button.setAttribute('aria-label', action.title || '');
            button.innerHTML = '<i class="bi ' + action.icon + '"></i>';
            button.disabled = true;
            button.addEventListener('click', function() {
                if (button.disabled) return;
                if (typeof action.onClick === 'function') action.onClick(state.ids.slice());
            });
            state.actionButtons.push({ button: button, action: action });
        });

        return group;
    };
    control.addTo(targetMap);
    sectorSelection = state;
}

function setSectorPickMode(targetMap, state, on) {
    state.pickMode = on;
    if (on) targetMap.closePopup();
    if (state.pickButton) {
        state.pickButton.classList.toggle('active', on);
        state.pickButton.setAttribute('aria-pressed', String(on));
    }
    targetMap.getContainer().classList.toggle('geometry-sector-pick-mode', on);

    // Disable the overlay polygons (zones, boundary, ...) so clicks pass
    // through to the sector polygons underneath while picking.
    state.blockLayers.forEach(function(layer) {
        if (!layer._path) return;
        if (on) {
            L.DomUtil.removeClass(layer._path, 'leaflet-interactive');
        } else {
            L.DomUtil.addClass(layer._path, 'leaflet-interactive');
        }
    });

    // Raise sector paths so they sit above the (now non-interactive) overlays.
    if (on) state.layers.forEach(function(layer) { if (layer.bringToFront) layer.bringToFront(); });
}

function registerSectorLayer(layer, properties) {
    if (!sectorSelection) return;
    const id = String(properties.id);
    sectorSelection.layers.set(id, layer);
    layer._sectorBaseStyle = { color: properties.color || '#3388ff', weight: 2, fillOpacity: 0.2 };
    layer.on('click', function(event) {
        if (!sectorSelection.pickMode || mapRulerActive) return;
        if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
        toggleSectorSelection(id);
    });
}

function toggleSectorSelection(id) {
    if (!sectorSelection) return;
    const index = sectorSelection.ids.indexOf(id);
    const layer = sectorSelection.layers.get(id);
    if (index === -1) {
        sectorSelection.ids.push(id);
        if (layer) applySectorStyle(layer, true);
    } else {
        sectorSelection.ids.splice(index, 1);
        if (layer) applySectorStyle(layer, false);
    }
    updateSectorActionButtons(sectorSelection);
    if (typeof sectorSelection.config.onSelectionChange === 'function') {
        sectorSelection.config.onSelectionChange(sectorSelection.ids.slice());
    }
}

function applySectorStyle(layer, selected) {
    if (!layer || !layer.setStyle) return;
    if (selected) {
        layer.setStyle({ color: '#f08c00', weight: 5, fillColor: '#f08c00', fillOpacity: 0.35 });
        if (layer.bringToFront) layer.bringToFront();
    } else {
        layer.setStyle(layer._sectorBaseStyle || { color: '#3388ff', weight: 2, fillOpacity: 0.2 });
    }
}

function updateSectorActionButtons(state) {
    const count = state.ids.length;
    state.actionButtons.forEach(function(item) {
        item.button.disabled = item.action.enabledWhen
            ? !item.action.enabledWhen(count)
            : count === 0;
    });
}

function initRulerTool(drawControl) {
    const rulerLayer = L.layerGroup().addTo(map);
    const state = { points: [], line: null, preview: null, cursor: null, finished: false };

    // Button joins the Leaflet.draw toolbar (polyline / polygon / marker / ruler)
    const toolbar = drawControl._container.querySelector('.leaflet-draw-toolbar');
    const button = L.DomUtil.create('a', 'geometry-ruler-button');
    button.href = '#';
    button.title = 'Measure distance (double-click to finish) / قياس المسافة';
    button.setAttribute('role', 'button');
    button.setAttribute('aria-label', 'Measure distance');
    button.innerHTML = '<i class="bi bi-rulers"></i>';
    const marker = toolbar.querySelector('.leaflet-draw-draw-marker');
    if (marker) marker.insertAdjacentElement('afterend', button);
    else toolbar.appendChild(button);
    L.DomEvent.disableClickPropagation(button);

    function totalLength(points) {
        let total = 0;
        for (let i = 1; i < points.length; i++) total += points[i - 1].distanceTo(points[i]);
        return total;
    }

    function resetMeasurement() {
        rulerLayer.clearLayers();
        state.points = [];
        state.line = null;
        state.preview = null;
        state.cursor = null;
        state.finished = false;
    }

    function addVertexLabel(latlng, text, permanentClass) {
        const dot = L.circleMarker(latlng, {
            radius: 4, color: '#c92a2a', weight: 2, fillColor: '#fff', fillOpacity: 1, interactive: false
        }).addTo(rulerLayer);
        dot.bindTooltip(text, {
            permanent: true, direction: 'right', offset: [8, 0],
            className: 'geometry-ruler-tooltip ' + (permanentClass || '')
        }).openTooltip();
        return dot;
    }

    function setActive(active) {
        mapRulerActive = active;
        button.classList.toggle('active', active);
        map.getContainer().classList.toggle('geometry-map-ruler-mode', active);
        if (active) {
            // Stop any running draw tool and selection mode interference
            Object.values(drawControl._toolbars || {}).forEach(function(tb) {
                if (tb && typeof tb.disable === 'function') tb.disable();
            });
            map.doubleClickZoom.disable();
            map.closePopup();
        } else {
            map.doubleClickZoom.enable();
            resetMeasurement();
        }
    }

    function finish() {
        if (state.finished || !state.points.length) return;
        state.finished = true;
        if (state.preview) { rulerLayer.removeLayer(state.preview); state.preview = null; }
        if (state.cursor) { rulerLayer.removeLayer(state.cursor); state.cursor = null; }
        const last = state.points[state.points.length - 1];
        if (state.points.length > 1) {
            addVertexLabel(last, 'Total / المجموع: ' + formatDistanceM(totalLength(state.points)), 'total');
        }
    }

    L.DomEvent.on(button, 'click', function(event) {
        L.DomEvent.stop(event);
        setActive(!mapRulerActive);
    });

    // A Leaflet.draw tool starting cancels the ruler
    map.on(L.Draw.Event.DRAWSTART, function() { if (mapRulerActive) setActive(false); });

    map.on('click', function(event) {
        if (!mapRulerActive) return;
        if (state.finished) resetMeasurement();

        const latlng = event.latlng;
        const last = state.points[state.points.length - 1];
        if (last && last.distanceTo(latlng) < 0.05) return;   // ignore the duplicate click of a double-click

        state.points.push(latlng);
        if (!state.line) {
            state.line = L.polyline(state.points, { color: '#c92a2a', weight: 3, interactive: false }).addTo(rulerLayer);
        } else {
            state.line.setLatLngs(state.points);
        }
        addVertexLabel(latlng, state.points.length === 1 ? 'Start / البداية' : formatDistanceM(totalLength(state.points)));
    });

    map.on('mousemove', function(event) {
        if (!mapRulerActive || state.finished || !state.points.length) return;
        const last = state.points[state.points.length - 1];
        if (!state.preview) {
            state.preview = L.polyline([last, event.latlng], {
                color: '#c92a2a', weight: 2, dashArray: '6 6', interactive: false
            }).addTo(rulerLayer);
            state.cursor = L.circleMarker(event.latlng, { radius: 0, opacity: 0, fillOpacity: 0, interactive: false }).addTo(rulerLayer);
            state.cursor.bindTooltip('', {
                permanent: true, direction: 'right', offset: [12, 0], className: 'geometry-ruler-tooltip live'
            }).openTooltip();
        }
        state.preview.setLatLngs([last, event.latlng]);
        state.cursor.setLatLng(event.latlng);
        const segment = last.distanceTo(event.latlng);
        state.cursor.setTooltipContent(
            formatDistanceM(totalLength(state.points) + segment) + ' (+' + formatDistanceM(segment) + ')'
        );
    });

    map.on('dblclick', function() { if (mapRulerActive) finish(); });

    document.addEventListener('keydown', function(event) {
        if (!mapRulerActive) return;
        if (event.key === 'Enter') finish();
        if (event.key === 'Escape') setActive(false);
    });
}

function initDrawTools(drawType, onDrawComplete) {
    if (!map) return;

    const drawOptions = {
        polygon: {
            allowIntersection: false,
            showArea: true
        },
        polyline: {
            allowIntersection: false
        },
        point: {}
    };

    const drawHandler = {
        polygon: new L.Draw.Polygon(map, drawOptions.polygon),
        polyline: new L.Draw.Polyline(map, drawOptions.polyline),
        point: new L.Draw.Marker(map, drawOptions.point)
    };

    if (drawHandler[drawType]) {
        drawHandler[drawType].enable();
    }

    map.on(L.Draw.Event.CREATED, function(e) {
        drawnItems.clearLayers();
        drawnItems.addLayer(e.layer);

        const geojson = e.layer.toGeoJSON();

        if (onDrawComplete) {
            let coords;
            if (drawType === 'point') {
                coords = geojson.geometry.coordinates;
            } else if (drawType === 'polyline') {
                coords = geojson.geometry.coordinates;
            } else if (drawType === 'polygon') {
                coords = geojson.geometry.coordinates[0];
            }
            onDrawComplete(coords, JSON.stringify(coords));
        }
    });
}

function clearDrawn() {
    if (drawnItems) {
        drawnItems.clearLayers();
    }
}

function addLegend(legendItems, options) {
    if (!map) return;
    const legend = L.control({ position: (options && options.position) || 'bottomright' });
    legend.onAdd = function() {
        const div = L.DomUtil.create('div', 'map-legend');
        let html = '<h6>Legend / مفتاح الخريطة</h6>';
        legendItems.forEach(item => {
            html += `<div class="legend-item"><span class="legend-color" style="background:${item.color}"></span> ${item.label}</div>`;
        });
        div.innerHTML = html;
        return div;
    };
    legend.addTo(map);
}

// ---------- Elevation heatmap ----------
// The elevation model is a grid of samples about 30 m apart.  Drawn as circles
// that meet at that spacing the samples join into a continuous terrain surface
// instead of a field of dots, and a radius in metres keeps the same ground
// covered at every zoom level.  The layer lives under the feature layer (own
// pane) and is not interactive, so sector popups and clicks still work.
const ELEVATION_GRADIENT = 'linear-gradient(90deg, #2f9e44 0%, #fcc419 50%, #e03131 100%)';

function haversineMeters(a, b) {
    const radius = 6371000;
    const toRad = Math.PI / 180;
    const lat1 = a[1] * toRad;
    const lat2 = b[1] * toRad;
    const dLat = (b[1] - a[1]) * toRad;
    const dLng = (b[0] - a[0]) * toRad;
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
    return 2 * radius * Math.asin(Math.min(1, Math.sqrt(h)));
}

// Median nearest-neighbour distance of the sample grid, measured on a bounded
// subset so a very dense model does not cost a quadratic pass.
function elevationSampleSpacingM(features) {
    const coords = features
        .map(function(feature) { return feature.geometry && feature.geometry.coordinates; })
        .filter(Boolean);
    if (coords.length < 2) return 0;
    const step = Math.max(1, Math.ceil(coords.length / 240));
    const picked = coords.filter(function(_, index) { return index % step === 0; });
    const distances = [];
    picked.forEach(function(point) {
        let nearest = Infinity;
        coords.forEach(function(other) {
            if (other === point) return;
            const distance = haversineMeters(point, other);
            if (distance > 0 && distance < nearest) nearest = distance;
        });
        if (isFinite(nearest)) distances.push(nearest);
    });
    if (!distances.length) return 0;
    distances.sort(function(a, b) { return a - b; });
    return distances[Math.floor(distances.length / 2)];
}

function formatElevation(value) {
    return (Math.round(value * 10) / 10).toLocaleString('en-US') + ' m';
}

function colorWithAlpha(color, alpha) {
    const match = /^#?([0-9a-f]{6})$/i.exec(String(color || '').trim());
    if (!match) return 'rgba(252, 196, 25, ' + alpha + ')';
    const value = parseInt(match[1], 16);
    return 'rgba(' + ((value >> 16) & 255) + ', ' + ((value >> 8) & 255) + ', ' +
        (value & 255) + ', ' + alpha + ')';
}

// Paint the samples into a canvas as soft colour blobs and lay it over their
// bounds.  A field of vector circles turned into bubbles as soon as the user
// zoomed in; a raster surface keeps the same soft gradients at every zoom, and
// the blobs blend into one terrain map instead of a grid of dots.
function buildElevationHeatOverlay(features, spacingM, paneName) {
    const located = features.filter(function(feature) {
        const coords = feature.geometry && feature.geometry.coordinates;
        return coords && isFinite(Number((feature.properties || {}).elevation_m));
    });
    if (!located.length) return null;

    const lats = located.map(function(f) { return f.geometry.coordinates[1]; });
    const lngs = located.map(function(f) { return f.geometry.coordinates[0]; });
    const minLat = Math.min.apply(null, lats);
    const maxLat = Math.max.apply(null, lats);
    const minLng = Math.min.apply(null, lngs);
    const maxLng = Math.max.apply(null, lngs);
    const spacing = spacingM > 0 ? spacingM : 30;

    // Half a sample of padding so the surface reaches past its outermost
    // points instead of ending on a hard rectangle.
    const latPad = spacing / 111320;
    const midLat = (minLat + maxLat) / 2;
    const lngPad = spacing / (111320 * Math.max(0.2, Math.cos(midLat * Math.PI / 180)));
    const south = minLat - latPad;
    const north = maxLat + latPad;
    const west = minLng - lngPad;
    const east = maxLng + lngPad;

    const width = 1024;
    const height = Math.max(256, Math.min(1024, Math.round(width * (north - south) / (east - west))));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    const xOf = function(lng) { return ((lng - west) / (east - west)) * width; };
    const yOf = function(lat) { return ((north - lat) / (north - south)) * height; };
    // lngPad is the sample spacing expressed in degrees at this latitude, so the
    // blobs stay one sample wide whatever the canvas resolution is.
    const radius = Math.max(2, (lngPad / (east - west)) * width * 0.95);

    located.forEach(function(feature) {
        const coords = feature.geometry.coordinates;
        const color = (feature.properties || {}).color || '#fcc419';
        const x = xOf(coords[0]);
        const y = yOf(coords[1]);
        const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
        gradient.addColorStop(0, colorWithAlpha(color, 0.85));
        gradient.addColorStop(0.55, colorWithAlpha(color, 0.5));
        gradient.addColorStop(1, colorWithAlpha(color, 0));
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(x, y, radius, 0, Math.PI * 2);
        ctx.fill();
    });

    return L.imageOverlay(canvas.toDataURL('image/png'),
        [[south, west], [north, east]], {
            pane: paneName,
            opacity: 0.8,
            interactive: false,
            className: 'elevation-heat-overlay'
        });
}

// Draw the cached elevation model as a coloured surface with its own toggle and
// legend.  options.spacingM skips the spacing measurement, options.onStats is
// called with {min, max, range, count} once the model arrives (or null when the
// project has no elevation data yet).
function addElevationHeatmap(targetMap, url, options) {
    options = options || {};
    if (!targetMap || !url) return null;

    const paneName = 'elevationHeatPane';
    if (!targetMap.getPane(paneName)) {
        const pane = targetMap.createPane(paneName);
        // Above the tile layer (200), below the vector overlay pane (400).
        pane.style.zIndex = 350;
    }

    const state = { layer: L.layerGroup(), visible: options.visible !== false, stats: null };
    let button = null;
    let lowLabel = null;
    let highLabel = null;
    let rangeLabel = null;

    function paint() {
        if (!button) return;
        button.classList.toggle('active', state.visible);
        button.setAttribute('aria-pressed', String(state.visible));
        button.title = (state.visible ? 'Hide' : 'Show') + ' elevation heatmap / خريطة الارتفاع';
        if (state.visible) {
            if (!targetMap.hasLayer(state.layer)) state.layer.addTo(targetMap);
        } else if (targetMap.hasLayer(state.layer)) {
            targetMap.removeLayer(state.layer);
        }
    }

    const control = L.control({ position: 'topleft' });
    control.onAdd = function() {
        const group = L.DomUtil.create('div', 'leaflet-bar map-elevation-widget');
        L.DomEvent.disableClickPropagation(group);
        L.DomEvent.disableScrollPropagation(group);
        button = L.DomUtil.create('button', 'map-elevation-button', group);
        button.type = 'button';
        button.innerHTML = '<i class="bi bi-thermometer-half"></i>';
        button.setAttribute('aria-label', 'Toggle elevation heatmap');
        L.DomEvent.on(button, 'click', function(event) {
            L.DomEvent.stop(event);
            if (button.disabled) return;
            state.visible = !state.visible;
            paint();
        });
        return group;
    };
    control.addTo(targetMap);

    const legend = L.control({ position: 'bottomright' });
    legend.onAdd = function() {
        const div = L.DomUtil.create('div', 'map-legend map-elevation-legend');
        div.innerHTML =
            '<h6>Elevation / الارتفاع</h6>' +
            '<div class="elevation-gradient" style="background:' + ELEVATION_GRADIENT + '"></div>' +
            '<div class="elevation-scale">' +
            '  <span data-role="low">Low / منخفض</span>' +
            '  <span data-role="high">High / عالي</span>' +
            '</div>' +
            '<div class="elevation-range" data-role="range"></div>';
        lowLabel = div.querySelector('[data-role="low"]');
        highLabel = div.querySelector('[data-role="high"]');
        rangeLabel = div.querySelector('[data-role="range"]');
        return div;
    };
    legend.addTo(targetMap);

    paint();

    fetch(url)
        .then(function(response) { return response.json(); })
        .then(function(data) {
            const features = ((data && data.features) || []).filter(function(feature) {
                return feature.geometry && feature.geometry.coordinates;
            });
            const elevations = features
                .map(function(feature) { return Number((feature.properties || {}).elevation_m); })
                .filter(function(value) { return isFinite(value); });

            if (!features.length || !elevations.length) {
                // No terrain data yet: leave the toggle visible but inert so the
                // page still shows that elevation is part of this map.
                if (button) {
                    button.disabled = true;
                    button.title = 'No elevation data yet / لا توجد بيانات ارتفاع بعد';
                }
                if (rangeLabel) rangeLabel.textContent = 'No elevation data / لا توجد بيانات';
                if (typeof options.onStats === 'function') options.onStats(null);
                return;
            }

            const min = Math.min.apply(null, elevations);
            const max = Math.max.apply(null, elevations);
            const spacing = Number(options.spacingM) || elevationSampleSpacingM(features);
            const overlay = buildElevationHeatOverlay(features, spacing, paneName);
            if (overlay) state.layer.addLayer(overlay);
            state.stats = { min: min, max: max, range: max - min, count: features.length };

            if (lowLabel) lowLabel.textContent = formatElevation(min);
            if (highLabel) highLabel.textContent = formatElevation(max);
            if (rangeLabel) {
                rangeLabel.textContent = 'Range / المدى: ' + formatElevation(max - min) +
                    ' · ' + features.length + ' samples';
            }
            if (typeof options.onStats === 'function') options.onStats(state.stats);
        })
        .catch(function(error) {
            console.error('Elevation heatmap error:', error);
            if (button) button.disabled = true;
            if (rangeLabel) rangeLabel.textContent = 'Elevation unavailable / الارتفاع غير متاح';
            if (typeof options.onStats === 'function') options.onStats(null);
        });

    return {
        layer: state.layer,
        stats: function() { return state.stats; },
        setVisible: function(visible) { state.visible = !!visible; paint(); }
    };
}

// ---------- Fullscreen map ----------
function toggleMapFullscreen(mapId) {
    const id = mapId || 'map';
    const container = document.getElementById(id);
    if (!container) return;

    const isFull = container.classList.toggle('map-fullscreen-active');
    document.body.classList.toggle('map-fullscreen-lock', isFull);

    document.querySelectorAll('.map-fullscreen-btn').forEach(function (btn) {
        if (btn.getAttribute('data-map-target') !== id) return;
        btn.classList.toggle('active', isFull);
        const icon = btn.querySelector('i');
        if (icon) {
            icon.classList.toggle('bi-arrows-fullscreen', !isFull);
            icon.classList.toggle('bi-fullscreen-exit', isFull);
        }
        btn.setAttribute('title', isFull ? 'Exit Full Page / خروج من ملء الشاشة' : 'Full Page / ملء الشاشة');
    });

    const instance = mapRegistry[id];
    if (instance) {
        setTimeout(function () { instance.invalidateSize(); }, 200);
    }
}
window.toggleMapFullscreen = toggleMapFullscreen;

document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    const fullscreenMap = document.querySelector('.map-fullscreen-active');
    if (fullscreenMap) toggleMapFullscreen(fullscreenMap.id);
});
