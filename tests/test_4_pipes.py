"""test-4 - Pipes tab: Main Pipe (MP), Second Pipe (SP), Zone Pipe (ZP)."""

import re

import pytest


PIPE_TABS = (
    ('main_pipe', 'Main Pipe (MP)'),
    ('second_pipe', 'Second Pipe (SP)'),
    ('sub_pipe', 'Zone Pipe (ZP)'),
    ('dripline', 'DripLine (DL)'),
)


@pytest.mark.end_to_end
def test_pipe_subtabs_render_for_every_project(ui, dataset):
    for project in dataset['projects']:
        for slug, label in PIPE_TABS[:3]:
            ui.goto(f'/{project["id"]}/{slug}')
            ui.assert_no_server_error(f'{slug} of project {project["id"]}')
            subnav = ui.page.locator('nav.project-workflow-subnav').first.inner_text()
            for _, name in PIPE_TABS:
                assert name in subnav, f'{name} missing from the Pipes sub-nav'
            assert ui.page.locator('[data-bs-target="#aiPipingModal"]').count() == 1


@pytest.mark.end_to_end
def test_auto_generate_the_pipe_network(ui, dataset):
    pid = dataset['projects'][0]['id']

    # MP: the Auto Generate button builds the main pipe from the water source.
    ui.goto(f'/{pid}/main_pipe')
    ui.submit_modal('aiPipingModal')
    flash = ui.expect_flash(r'Main pipe generated: \d+ main pipe', kind='success',
                            timeout=120_000)
    ui.assert_no_danger('main pipe generation')
    assert int(next(iter(re.findall(r'\d+', flash)))) >= 1
    assert 'MP' in ui.body_text() or 'Main Pipe' in ui.body_text()
    ui.shot('main-pipe-generated')

    # SP: second pipes leave the main pipe towards the sectors.
    ui.goto(f'/{pid}/second_pipe')
    ui.submit_modal('aiPipingModal')
    ui.expect_flash(r'Second pipes generated: \d+ second pipe', kind='success',
                    timeout=120_000)
    ui.assert_no_danger('second pipe generation')
    ui.shot('second-pipes-generated')

    # ZP: zone pipes and the drip lines that feed the rows.
    ui.goto(f'/{pid}/sub_pipe')
    ui.submit_modal('aiPipingModal')
    flash = ui.expect_flash(r'Zone pipes generated: \d+ zone pipe', kind='success',
                            timeout=120_000)
    ui.assert_no_danger('zone pipe generation')
    assert int(next(iter(re.findall(r'\d+', flash)))) >= 1
    ui.shot('zone-pipes-generated')


@pytest.mark.end_to_end
def test_dripline_stage_waits_for_validated_rows(ui, dataset):
    """The DripLine sub-tab is disabled until the Rows tab is validated."""
    project = dataset['projects'][-1]
    ui.goto(f'/{project["id"]}/main_pipe')

    subnav = ui.page.locator('nav.project-workflow-subnav').first
    dripline = subnav.locator('text=DripLine').first
    assert dripline.count() == 1
    # A disabled tab is rendered as a span, an enabled one as a link.
    assert dripline.evaluate("el => el.closest('a') === null"), \
        'DripLine should be disabled while the rows are not validated'
