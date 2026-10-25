"""test-2 - Water Resource tab: Well, Basin and Other sub-tabs."""

import pytest


SUBTABS = (('well', 'Well'), ('basin', 'Basin'), ('other', 'Other'))


@pytest.mark.end_to_end
def test_water_subtabs_render_for_every_project(ui, dataset):
    for project in dataset['projects']:
        for slug, label in SUBTABS:
            ui.goto(f'/water/{project["id"]}/{slug}')
            ui.assert_no_server_error(f'water/{slug} of project {project["id"]}')
            ui.assert_no_danger(f'water/{slug} of project {project["id"]}')

            subnav = ui.page.locator('nav.project-workflow-subnav')
            assert subnav.count() == 1, f'no Water Resource sub-nav on {slug}'
            subnav_text = subnav.first.inner_text()
            for _, name in SUBTABS:
                assert name in subnav_text

            # The circle Add button of the table toolbar opens the Add modal.
            assert ui.page.locator('[data-bs-target="#addSourceModal"]').count() == 1


@pytest.mark.end_to_end
def test_add_a_well_from_the_water_tab(ui, dataset):
    project = dataset['projects'][0]
    ui.goto(f'/water/{project["id"]}/well')

    ui.submit_modal('addSourceModal', {
        'name': 'PW Well Alpha',
        'lat': '33.6171',
        'lng': '-4.7272',
        'flow_lpm': '120',
        'well_depth_m': '45',
        'well_diameter_mm': '300',
    })

    ui.expect_flash(r'Added', kind='success')
    ui.assert_no_danger('adding a well')
    body = ui.body_text()
    assert 'PW Well Alpha' in body
    assert '45.0' in body and '300' in body, 'the well depth / diameter are not in the table'
    ui.shot('well-added')

    # Validating the tab is a separate step of the same page. A source whose
    # data is fine is reported as success, an incomplete one as a warning.
    ui.submit_form('/well/validate')
    ui.expect_flash(r'alidated')


@pytest.mark.end_to_end
def test_basin_tab_accepts_a_basin_with_volume_fields(ui, dataset):
    project = dataset['projects'][0]
    ui.goto(f'/water/{project["id"]}/basin')

    ui.submit_modal('addSourceModal', {
        'name': 'PW Basin Alpha',
        'basin_length_m': '40',
        'basin_width_m': '25',
        'basin_depth_m': '3',
    })
    ui.expect_flash(r'Added', kind='success')
    ui.assert_no_danger('adding a basin')

    body = ui.body_text()
    assert 'PW Basin Alpha' in body
    assert '40.0 × 25.0' in body, 'the basin size is not shown in the table'
    # 40 m x 25 m x 3 m = 3 000 m3
    assert '3,000' in body or '3000' in body, 'basin volume is not shown in the table'
    ui.shot('basin-added')
