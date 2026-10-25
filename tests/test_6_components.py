"""test-6 - Components tab: valves and the other component sub-tabs."""

import pytest


COMPONENT_TABS = (
    ('valves', 'Valves'),
    ('connectors', 'Connectors'),
    ('reductors', 'Reductors'),
    ('air_removal', 'Air Removal'),
    ('pressure_reduction', 'Reduce Pressure'),
    ('terminators', 'Terminators'),
)


@pytest.mark.end_to_end
def test_component_subtabs_render_for_every_project(ui, dataset):
    for project in dataset['projects']:
        for slug, label in COMPONENT_TABS:
            ui.goto(f'/components/{project["id"]}/{slug}')
            ui.assert_no_server_error(f'{slug} of project {project["id"]}')
            assert label in ui.body_text()
            subnav = ui.page.locator('nav.project-workflow-subnav').first.inner_text()
            for _, name in COMPONENT_TABS:
                assert name in subnav


@pytest.mark.end_to_end
def test_generate_valves_from_the_pipes(ui, dataset):
    pid = dataset['projects'][0]['id']
    ui.goto(f'/components/{pid}/valves')

    rows = ui.page.locator('#valvesTable tbody tr[data-valve-type]')
    before = rows.count()
    ui.submit_modal('generateValvesModal', {'replace': True})
    flash = ui.expect_flash(r'(Generated \d+ master|No valve generated)', timeout=120_000)
    ui.assert_no_danger('valve generation')
    assert 'Generated' in flash, f'no valve was placed: {flash!r}'

    after = rows.count()
    assert after > before, f'the valves table did not grow ({before} -> {after})'
    assert after >= 1
    ui.shot('valves-generated')


@pytest.mark.end_to_end
def test_other_components_have_the_same_auto_generate_button(ui, dataset):
    pid = dataset['projects'][0]['id']
    for slug, label in COMPONENT_TABS[1:]:
        ui.goto(f'/components/{pid}/{slug}')
        assert ui.page.locator('[data-bs-target="#generateComponentModal"]').count() == 1, \
            f'{slug} has no Auto Generate circle button'

        ui.submit_modal('generateComponentModal', {'replace': True})
        ui.expect_flash(r'(generated|Nothing generated)', timeout=120_000)
        ui.assert_no_danger(f'generating {slug}')
        ui.shot(f'{slug}-generated')
