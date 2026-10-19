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
const TREE_LABEL_MIN_ZOOM = 17;

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

function addFeatureLabel(layer, properties, group) {
    if (!group) return;
    const html = featureLabelHtml(properties);
    if (!html) return;
    const center = layerCenterLatLng(layer);
    if (!center) return;
    const marker = L.marker(center, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({ className: 'map-feature-label', html: html, iconSize: null })
    });
    marker.on('add', function() {
        const el = marker.getElement();
        if (el) {
            el.style.marginLeft = -(el.offsetWidth / 2) + 'px';
            el.style.marginTop = -(el.offsetHeight / 2) + 'px';
        }
    });
    marker.addTo(group);
}

// Tree labels are only rendered at high zoom levels to keep dense orchard maps usable.
function syncTreeLabels() {
    if (!map || !labelLayers || !labelLayers.tree) return;
    const group = labelLayers.tree;
    const show = labelState.tree && map.getZoom() >= TREE_LABEL_MIN_ZOOM;
    if (show) {
        if (!map.hasLayer(group)) group.addTo(map);
    } else if (map.hasLayer(group)) {
        map.removeLayer(group);
    }
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
// Pages that pass `mapFilters` get a funnel widget that hides whole geometry
// categories (sectors, zones, rows, trees, pipes) without reloading the map.
const FILTER_CATEGORIES = [
    { key: 'sector', letter: 'S', label: 'Sectors / القطاعات', types: ['sector'] },
    { key: 'zone', letter: 'Z', label: 'Zones / المناطق', types: ['zone'] },
    { key: 'tree_row', letter: 'R', label: 'Rows / الصفوف', types: ['tree_row'] },
    { key: 'tree', letter: 'T', label: 'Trees / الأشجار', types: ['tree'] },
    { key: 'pipe', letter: 'P', label: 'Pipes / الأنابيب', types: ['main_pipe', 'second_pipe', 'sub_pipe', 'dripline'] }
];
const filterState = { sector: true, zone: true, tree_row: true, tree: true, pipe: true };

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

// Funnel widget with one circle button per filterable category.
function setupGeometryFilterWidget(targetMap, filterKeys) {
    const categories = FILTER_CATEGORIES.filter(function(category) {
        return filterKeys.indexOf(category.key) !== -1;
    });
    if (!categories.length) return;

    const control = L.control({ position: 'bottomleft' });
    control.onAdd = function() {
        const group = L.DomUtil.create('div', 'leaflet-bar map-filter-toggle');
        L.DomEvent.disableClickPropagation(group);
        L.DomEvent.disableScrollPropagation(group);

        const head = L.DomUtil.create('div', 'map-filter-toggle-head', group);
        head.innerHTML = '<i class="bi bi-funnel"></i>';
        head.title = 'Map filters / عوامل تصفية الخريطة';

        categories.forEach(function(category) {
            const button = L.DomUtil.create('button', 'geometry-map-select-button map-filter-toggle-button', group);
            button.type = 'button';
            button.textContent = category.letter;
            button.title = 'Hide ' + category.label;
            button.setAttribute('aria-label', 'Toggle ' + category.label);
            button.setAttribute('aria-pressed', 'true');
            button.classList.add('active');
            L.DomEvent.on(button, 'click', function(event) {
                L.DomEvent.stop(event);
                filterState[category.key] = !filterState[category.key];
                const visible = filterState[category.key];
                button.classList.toggle('active', visible);
                button.setAttribute('aria-pressed', String(visible));
                button.title = (visible ? 'Hide ' : 'Show ') + category.label;
                applyGeometryFilter(category.key, visible);
            });
        });

        return group;
    };
    control.addTo(targetMap);
}

// Reload the current geometry without a page navigation (rows/orientation edits).
window.reloadMapGeometry = function() {
    if (!currentGeojsonUrl || !featureLayer) return Promise.resolve(false);
    return fetch(currentGeojsonUrl)
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (labelLayers) {
                Object.keys(labelLayers).forEach(function(type) {
                    labelLayers[type].clearLayers();
                });
            }
            featureLayer.clearLayers();
            featureLayer.addData(data);
            applyAllGeometryFilters();
            syncTreeLabels();
            return true;
        })
        .catch(function(err) {
            console.error('Error reloading geometry:', err);
            return false;
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

function initMap(elementId, geojsonUrl, options = {}) {
    const mapElement = document.getElementById(elementId);
    if (!mapElement) return null;
    currentGeojsonUrl = geojsonUrl || currentGeojsonUrl;

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

    // Popups should not compete with the ruler while measuring
    map.on('popupopen', function() {
        if (mapRulerActive) map.closePopup();
    });

    mapSelectionMode = false;
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

    // Tree labels only appear from a high zoom level to keep orchard maps readable.
    map.on('zoomend', syncTreeLabels);

    // Circle-button widget to toggle each label category.
    setupLabelToggleWidget(map, labelTypes);

    // Optional funnel widget that hides whole geometry categories.
    if (Array.isArray(options.mapFilters) && options.mapFilters.length) {
        setupGeometryFilterWidget(map, options.mapFilters);
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
                let popupContent = `<b>${escapeLabelHtml(labelCode(properties) || name)}</b>`;
                if (name && labelCode(properties)) popupContent += ` &ndash; ${escapeLabelHtml(name)}`;
                if (nameAr) popupContent += ` / <span class="ar-text">${nameAr}</span>`;
                popupContent += `<br><small>Type: ${type}</small>`;
                if (validated !== undefined) {
                    popupContent += `<br><small>Status: ${validated ? '✓ Validated' : 'Pending'}</small>`;
                }
                if (options.showArea) {
                    const stats = geometryAreaStats(feature.geometry);
                    if (stats) {
                        popupContent += `<br><small><b>Area / المساحة:</b> ${formatAreaM2(stats.area)}</small>`;
                        popupContent += `<br><small><b>Perimeter / المحيط:</b> ${formatDistanceM(stats.perimeter)}</small>`;
                    }
                }
                layer.bindPopup(popupContent);

                if (options.selectable && selectableTypes.indexOf(type) !== -1 && properties.id !== undefined) {
                    const checkbox = Array.from(document.querySelectorAll(options.selectable.checkboxSelector))
                        .find((input) => input.value === String(properties.id));
                    const setSelectedStyle = (selected) => {
                        layer._geometrySelected = selected;
                        if (layer.setStyle) {
                            layer.setStyle({
                                color: selected ? '#f08c00' : properties.color || '#3388ff',
                                weight: selected ? 4 : 2,
                                fillOpacity: selected ? 0.4 : 0.2
                            });
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
                    addFeatureLabel(layer, properties, labelLayers[type]);
                }
            }
        }
    }).addTo(map);

    // Load GeoJSON data if URL provided
    if (geojsonUrl) {
        fetch(geojsonUrl)
            .then(response => response.json())
            .then(data => {
                featureLayer.addData(data);
                if (data.features && data.features.length > 0) {
                    map.fitBounds(featureLayer.getBounds(), { padding: [50, 50] });
                }
                applyAllGeometryFilters();
                syncTreeLabels();
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

function addLegend(legendItems) {
    if (!map) return;
    const legend = L.control({ position: 'bottomright' });
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
