"""test-1 - assign/link one elevation map to each project.

Every project created by the fixture gets exactly one KML from ``maps/`` whose
name contains "elevation", uploaded through the Project page file input.
"""

import re
from pathlib import Path

import pytest

from support import MAPS_DIR


@pytest.mark.end_to_end
def test_every_project_has_one_elevation_map(ui, dataset):
    projects = dataset['projects']
    maps = [Path(path) for path in dataset['maps']]

    assert maps, (f'no KML whose name contains "elevation" was found in {MAPS_DIR}')
    if len(maps) < len(projects):
        print(f'\n  note: {len(maps)} elevation KML in maps/, '
              f'{len(projects)} projects - files are reused in order')

    for index, project in enumerate(projects):
        map_path = maps[index % len(maps)]
        ui.goto(f'/{project["id"]}')

        form = ui.page.locator("form[action*='upload_kml']")
        assert form.count() == 1, f'project {project["id"]} has no KML upload form'
        form.locator('input[type=file]').set_input_files(str(map_path))
        form.locator('button[type=submit]').click()
        ui.page.wait_for_load_state('domcontentloaded')

        flash = ui.expect_flash(r'Loaded', timeout=30_000)
        ui.assert_no_danger(f'project {project["id"]} upload')
        assert 'Could not parse' not in flash

        body = ui.body_text()
        assert map_path.name in body, (
            f'{map_path.name} is not shown as the project map after upload')
        elements = ui.page.locator('.badge', has_text='elements').first.inner_text()
        count = int(next(iter(re.findall(r'\d+', elements)), '0'))
        assert count > 0, f'uploaded map reports no element: {elements!r}'

        project['map'] = map_path.name
        ui.shot(f'map-project-{project["id"]}')

    assigned = sorted({project['map'] for project in projects})
    assert len(assigned) == min(len(maps), len(projects))


@pytest.mark.end_to_end
def test_map_survives_a_page_reload(ui, dataset):
    project = dataset['projects'][0]
    ui.goto(f'/{project["id"]}')
    body = ui.body_text()
    assert project['map'] in body
    assert 'KML' in body
