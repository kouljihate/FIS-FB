"""test-3 - Geometry tab: sectors and zones."""

import re

import pytest


@pytest.mark.end_to_end
def test_geometry_subtabs_render_for_every_project(ui, dataset):
    for project in dataset['projects']:
        for slug, heading in (('sectors', 'Sectors'), ('zones', 'Zones')):
            ui.goto(f'/{project["id"]}/{slug}')
            ui.assert_no_server_error(f'{slug} of project {project["id"]}')
            assert heading in ui.body_text()
            subnav = ui.page.locator('nav.project-workflow-subnav').first.inner_text()
            assert 'Sectors' in subnav and 'Zones' in subnav


@pytest.mark.end_to_end
def test_generate_sectors_and_zones(ui, dataset):
    project = dataset['projects'][0]
    pid = project['id']

    ui.goto(f'/{pid}/sectors')
    assert ui.page.locator('[data-bs-target="#aiGenerateModal"]').count() == 1, \
        'the Auto Generate circle button is missing from the Sectors tab'

    ui.submit_modal('aiGenerateModal', {'n_sectors': '4'})
    flash = ui.expect_flash(r'Auto Generated \d+ sectors', kind='success', timeout=60_000)
    ui.assert_no_danger('sector generation')
    generated = int(next(iter(re.findall(r'\d+', flash))))
    assert generated >= 2, f'only {generated} sectors were generated'

    body = ui.body_text()
    assert 'Sector A' in body, 'the generated sectors are not listed'
    assert ui.page.locator('table tbody tr').count() >= generated
    ui.shot('sectors-generated')

    ui.submit_form('/sectors/validate')
    ui.expect_flash(r'All sectors validated', kind='success')
    ui.goto(f'/{pid}/sectors')
    assert ui.page.locator('.badge.bg-success').count() >= generated, \
        'validated sectors do not show their green badge'

    # Zones live in their own sub-tab and are generated per sector.
    ui.goto(f'/{pid}/zones')
    assert ui.page.locator('[data-bs-target="#aiGenerateZoneModal"]').count() == 1
    ui.submit_modal('aiGenerateZoneModal', {'zones_per_sector': '2'})
    flash = ui.expect_flash(r'Auto Generated \d+ zones', kind='success', timeout=60_000)
    ui.assert_no_danger('zone generation')
    zones = int(next(iter(re.findall(r'\d+', flash))))
    assert zones >= generated, f'{zones} zones for {generated} sectors'
    assert 'Zone 1' in ui.body_text()
    ui.shot('zones-generated')

    ui.submit_form('/zones/validate')
    ui.expect_flash(r'All zones validated', kind='success')
    # The zones validation step hands over to the Pipes tab.
    assert '/main_pipe' in ui.page.url
    ui.goto(f'/{pid}/zones')
    assert ui.page.locator('table tbody tr').count() >= zones
