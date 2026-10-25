"""test-7 - Map Overview: the map page and its widgets."""

import pytest


@pytest.mark.end_to_end
def test_overview_page_for_every_project(ui, dataset):
    for project in dataset['projects']:
        ui.goto(f'/overview/{project["id"]}')
        ui.assert_no_server_error(f'overview of project {project["id"]}')
        assert ui.page.locator('#map').count() == 1, 'the overview map container is missing'
        ui.assert_no_danger(f'overview of project {project["id"]}')


@pytest.mark.end_to_end
def test_overview_map_and_widgets(ui, dataset):
    project = dataset['projects'][0]
    ui.goto(f'/overview/{project["id"]}')

    leaflet = ui.page.evaluate("() => typeof window.L !== 'undefined'")
    if not leaflet:
        pytest.skip('Leaflet comes from a CDN and the network is unavailable')

    ui.page.wait_for_selector('.leaflet-container', timeout=30_000)
    ui.page.wait_for_selector('body.map-overview-ready', timeout=30_000)

    controls = ui.page.locator('.leaflet-control').count()
    assert controls >= 1, 'no map control (zoom / layers) was created'

    buttons = ui.page.locator('.map-overview-sector-btn').count()
    assert buttons >= 1, 'no sector widget button was created on the overview'
    ui.shot('overview-map')
