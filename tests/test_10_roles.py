"""test-10 - Roles: what an Admin, a Project Admin and a Project Owner may see.

* An Admin and a Project Admin see every project; only an Admin may open the
  Users page.
* A Project Owner sees the one project they own, and is sent back to the
  project list with a "Project not found" message for every other one.
* A Project Owner is a read-only viewer of their own project: they cannot
  create, edit or remove it, and only Map Overview, Estimation and Report are
  available to them (the other tabs redirect to Map Overview).

The file name sorts before ``test_1`` because it only needs the seeded
accounts; it does not touch the project data the pipeline builds.

Project pages sit at the root (``/{id}``, ``/{id}/sectors``, ...); the tabs that
own a prefix are ``/water/{id}/<tab>``, ``/components/{id}/<tab>``,
``/overview/{id}`` and ``/engineering/{id}``.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from support import Ui


@contextmanager
def signed_in(browser, dataset, request, account: dict, label: str):
    """A browser context signed in as ``account``, screenshotted like ``ui``."""
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(20_000)

    ui = Ui(page, dataset['base_url'], prefix=label)
    request.node._ui = ui
    page.on('dialog', lambda dialog: dialog.accept())
    page.on('console', lambda message: ui.console_errors.append(message.text)
            if message.type == 'error' else None)
    page.on('pageerror', lambda error: ui.page_errors.append(str(error)))

    base = dataset['base_url']
    page.goto(base + '/login', wait_until='commit')
    page.wait_for_selector('input[name="username"]', timeout=30_000)
    page.fill('input[name="username"]', account['username'])
    page.fill('input[name="password"]', account['password'])
    page.click('form button[type="submit"]', no_wait_after=True)
    page.wait_for_url(base.rstrip('/') + '/', timeout=60_000)
    page.wait_for_selector('body', state='attached')
    assert 'Invalid credentials' not in page.content(), (
        f'login failed for {account["username"]}')

    try:
        yield ui
    finally:
        request.node._pw_shots = (
            list(getattr(request.node, '_pw_shots', []) or []) + list(ui.shots))
        request.node._pw_console = (
            list(getattr(request.node, '_pw_console', []) or [])
            + ui.console_errors + ui.page_errors)[:40]
        context.close()


def project_of(dataset, username: str) -> dict:
    return next(p for p in dataset['projects'] if p['owner'] == username)


def post_from_page(ui, path: str, fields: dict) -> None:
    """Submit a plain POST form from the page, so the flash is readable."""
    try:
        ui.page.evaluate(
            """([action, data]) => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = action;
                form.style.display = 'none';
                for (const [name, value] of Object.entries(data)) {
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = name;
                    input.value = value;
                    form.appendChild(input);
                }
                document.body.appendChild(form);
                form.submit();
            }""", [path, fields])
    except Exception:
        # The submit tears the page down under the evaluator; that is expected.
        pass
    ui._settle()


def listed_project_names(ui) -> list[str]:
    """The Name column of the project table, one entry per row."""
    rows = ui.page.locator('table tbody tr')
    names = []
    for index in range(rows.count()):
        cells = rows.nth(index).locator('td')
        if cells.count() >= 2:
            names.append(cells.nth(1).inner_text().strip())
    return names


@pytest.mark.end_to_end
def test_owner_sees_only_their_own_project(browser, dataset, request):
    for admin in dataset['project_admins']:
        owner = admin['owners'][0]
        mine = project_of(dataset, owner['username'])
        others = [p['name'] for p in dataset['projects'] if p['id'] != mine['id']]

        with signed_in(browser, dataset, request, owner,
                       f'owner-{owner["username"]}-list') as ui:
            ui.goto('/')
            ui.assert_no_server_error('the project list')
            ui.assert_no_danger('the project list')

            names = listed_project_names(ui)
            assert names == [mine['name']], (
                f'{owner["username"]} should see exactly {mine["name"]!r}, got {names!r}')

            body = ui.body_text()
            leaked = [name for name in others if name in body]
            assert not leaked, f'{owner["username"]} can see foreign projects: {leaked}'
            ui.shot('owner-project-list')


@pytest.mark.end_to_end
def test_owner_cannot_open_a_foreign_project(browser, dataset, request):
    """Every kind of project page refuses a project the owner does not own."""
    admins = dataset['project_admins']
    paths = ('/{id}', '/{id}/sectors', '/water/{id}/well',
             '/overview/{id}', '/engineering/{id}')

    for index, admin in enumerate(admins):
        owner = admin['owners'][0]
        # One project from another local admin, so this is not just "my group".
        foreign_admin = admins[(index + 1) % len(admins)]
        foreign = project_of(dataset, foreign_admin['owners'][0]['username'])

        # A single login covers every page of this foreign project.
        for index_path, path in enumerate(paths):
            with signed_in(browser, dataset, request, owner,
                           f'owner-{owner["username"]}-foreign') as ui:
                ui.goto(path.format(id=foreign['id']))
                ui.expect_flash(r'not found', kind='danger')
                assert ui.page.url.rstrip('/') == dataset['base_url'].rstrip('/'), (
                    f'{path} of a foreign project was opened: {ui.page.url}')
                assert foreign['name'] not in ui.body_text(), (
                    f'{foreign["name"]} is visible through {path}')
                ui.assert_no_server_error(path)
                if index_path == 0:
                    ui.shot('foreign-project-refused')


@pytest.mark.end_to_end
def test_owner_own_project_still_opens(browser, dataset, request):
    """The refusal above is about ownership, not about the pages being broken."""
    owner = dataset['project_admins'][0]['owners'][1]
    mine = project_of(dataset, owner['username'])

    with signed_in(browser, dataset, request, owner, f'owner-{owner["username"]}-own') as ui:
        for path in ('/{id}', '/overview/{id}', '/estimation/{id}',
                     '/engineering/{id}/report/view', '/engineering/{id}/technical-report'):
            ui.goto(path.format(id=mine['id']))
            ui.assert_no_danger(path)
            ui.assert_no_server_error(path)
            assert 'Project not found' not in ui.body_text(), f'{path} refused its own project'
        ui.shot('owner-own-project')


@pytest.mark.end_to_end
def test_owner_tabs_are_map_overview_estimation_and_report(browser, dataset, request):
    """Only the three reading tabs are drawn for a Project Owner."""
    owner = dataset['project_admins'][0]['owners'][1]
    mine = project_of(dataset, owner['username'])

    with signed_in(browser, dataset, request, owner, f'owner-{owner["username"]}-tabs') as ui:
        ui.goto(f'/overview/{mine["id"]}')
        ui.assert_no_server_error('Map Overview')
        tabs = [text.strip() for text in
                ui.page.locator('.project-workflow-nav .nav-link').all_inner_texts()]
        labels = ' | '.join(tabs)
        assert len(tabs) == 3, f'a Project Owner should have three tabs, got {tabs!r}'
        for wanted in ('Map Overview', 'Estimation', 'Report'):
            assert wanted in labels, f'the {wanted} tab is missing: {labels}'
        for hidden in ('Water Resource', 'Geometry', 'Pipes', 'Trees', 'Components',
                       'Validation', 'Project'):
            assert hidden not in labels, f'a Project Owner can still see {hidden}: {labels}'
        ui.shot('owner-tabs')


@pytest.mark.end_to_end
def test_owner_build_tabs_redirect_to_map_overview(browser, dataset, request):
    """A typed URL for a tab the owner may not use lands on Map Overview."""
    owner = dataset['project_admins'][0]['owners'][1]
    mine = project_of(dataset, owner['username'])
    overview_url = f'/overview/{mine["id"]}'

    with signed_in(browser, dataset, request, owner, f'owner-{owner["username"]}-blocked') as ui:
        paths = ('/{id}/sectors', '/{id}/zones', '/water/{id}/well', '/water/{id}/basin',
                 '/{id}/main_pipe', '/{id}/second_pipe', '/{id}/sub_pipe', '/{id}/dripline',
                 '/{id}/rows', '/{id}/trees', '/components/{id}/valves', '/engineering/{id}')
        for path in paths:
            ui.goto(path.format(id=mine['id']))
            ui.assert_no_server_error(path)
            assert ui.page.url.endswith(overview_url), (
                f'{path} was not sent to Map Overview, it reached {ui.page.url}')
        ui.shot('owner-blocked-tab')


@pytest.mark.end_to_end
def test_owner_cannot_create_edit_or_remove_a_project(browser, dataset, request):
    """The owner's own project is read-only: it cannot be created, edited or removed."""
    owner = dataset['project_admins'][0]['owners'][1]
    mine = project_of(dataset, owner['username'])
    name = f'PW Owner Made {owner["username"]}'

    with signed_in(browser, dataset, request, owner, f'owner-{owner["username"]}-write') as ui:
        # The project list offers no create button, no link and no delete.
        ui.goto('/')
        assert 'Create Project' not in ui.body_text(), 'the owner can create a project'
        assert ui.page.locator('form[action$="/delete"]').count() == 0, \
            'the project list shows a delete button to the owner'

        # /create refuses the form and does nothing.
        ui.goto('/create')
        ui.expect_flash(r'cannot create, edit or remove', kind='danger')
        assert ui.page.url.rstrip('/') == dataset['base_url'].rstrip('/'), \
            f'the owner reached the create page: {ui.page.url}'

        post_from_page(ui, '/create', {'name': name, 'admin_user_id': str(owner['id'])})
        ui.expect_flash(r'cannot create, edit or remove', kind='danger')

        # Settings and delete refuse too.
        post_from_page(ui, f'/{mine["id"]}/settings', {'location': 'owner edit'})
        ui.expect_flash(r'cannot create, edit or remove', kind='danger')

        post_from_page(ui, f'/{mine["id"]}/delete', {})
        ui.expect_flash(r'cannot create, edit or remove', kind='danger')

        # No edit/delete affordance on the project page either.
        ui.goto(f'/{mine["id"]}')
        ui.assert_no_server_error('the project page')
        page = ui.page.content()
        assert 'editSpecModal' not in page, 'the owner still gets the settings modal'
        assert 'deleteProjectModal' not in page, 'the owner still gets the delete modal'
        ui.shot('owner-read-only-project')

    # The project is untouched and the refused create made nothing.
    with signed_in(browser, dataset, request, dataset['admin'], 'admin-after-owner-writes') as ui:
        ui.goto('/')
        names = listed_project_names(ui)
        assert mine['name'] in names, f'{mine["name"]} is gone: {names!r}'
        assert name not in names, 'the owner created a project anyway'
        assert mine['name'] in ui.body_text()


@pytest.mark.end_to_end
def test_geometry_api_is_scoped_to_the_owner(browser, dataset, request):
    """The map data endpoint answers 404 for a project the owner cannot open."""
    owner = dataset['project_admins'][0]['owners'][0]
    mine = project_of(dataset, owner['username'])
    foreign = project_of(dataset, dataset['project_admins'][1]['owners'][0]['username'])

    with signed_in(browser, dataset, request, owner, f'owner-{owner["username"]}-api') as ui:
        base = dataset['base_url']
        own = ui.page.request.get(f'{base}/api/project/{mine["id"]}/geometry?types=sector')
        assert own.status == 200, f'the owner cannot read their own geometry (HTTP {own.status})'

        blocked = ui.page.request.get(f'{base}/api/project/{foreign["id"]}/geometry?types=all')
        assert blocked.status == 404, (
            f'the geometry API served a foreign project (HTTP {blocked.status})')
        assert 'FeatureCollection' not in blocked.text(), 'the API returned foreign geometry'

        summary = ui.page.request.get(f'{base}/api/project/{foreign["id"]}/assistant/summary')
        assert summary.status == 404, (
            f'the assistant summary served a foreign project (HTTP {summary.status})')


@pytest.mark.end_to_end
def test_owner_cannot_delete_or_edit_a_foreign_project(browser, dataset, request):
    """Write endpoints refuse too, so the project is still intact afterwards.

    An owner may not edit *any* project, so the refusal is the read-only rule
    rather than the ownership one.
    """
    owner = dataset['project_admins'][0]['owners'][0]
    foreign = project_of(dataset, dataset['project_admins'][1]['owners'][0]['username'])
    base = dataset['base_url']

    with signed_in(browser, dataset, request, owner, f'owner-{owner["username"]}-write') as ui:
        ui.goto('/')
        for action, fields in (
                (f'/{foreign["id"]}/delete', {}),
                (f'/{foreign["id"]}/settings', {'location': 'hijacked', 'name': 'hijacked'})):
            post_from_page(ui, action, fields)
            ui.expect_flash(r'cannot create, edit or remove', kind='danger')
            assert ui.page.url.rstrip('/') == base.rstrip('/'), (
                f'{action} sent the owner to {ui.page.url}')
            ui.assert_no_server_error(action)
        ui.shot('foreign-write-refused')

    # An Admin still sees the project, under its own name.
    with signed_in(browser, dataset, request, dataset['admin'], 'admin-after-foreign-write') as ui:
        ui.goto('/')
        names = listed_project_names(ui)
        assert foreign['name'] in names, f'{foreign["name"]} is gone: {names!r}'
        assert 'hijacked' not in names, 'the foreign project was renamed by an owner'
        # The suite shares the application database, so other projects may be
        # listed too: every seeded project must still be there, under its name.
        missing = sorted({p['name'] for p in dataset['projects']} - set(names))
        assert not missing, f'projects missing after the refused writes: {missing}'


@pytest.mark.end_to_end
def test_local_admin_sees_every_project(browser, dataset, request):
    for index, admin in enumerate(dataset['project_admins']):
        other = dataset['project_admins'][(index + 1) % len(dataset['project_admins'])]
        foreign = project_of(dataset, other['owners'][0]['username'])

        with signed_in(browser, dataset, request, admin,
                       f'localadmin-{admin["username"]}') as ui:
            ui.goto('/')
            ui.assert_no_server_error('the project list')
            names = listed_project_names(ui)
            missing = sorted({p['name'] for p in dataset['projects']} - set(names))
            assert not missing, (
                f'a local admin should see all {len(dataset["projects"])} seeded '
                f'projects, missing {missing}; table holds {names!r}')

            ui.goto(f'/{foreign["id"]}')
            ui.assert_no_danger(f'the project of {other["username"]}`s owner')
            assert foreign['name'] in ui.body_text()
            ui.shot('local-admin-foreign-project')


@pytest.mark.end_to_end
def test_user_administration_is_admin_only(browser, dataset, request):
    local_admin = dataset['project_admins'][0]
    owner = local_admin['owners'][0]

    for account, label in ((local_admin, 'localadmin'), (owner, 'owner')):
        with signed_in(browser, dataset, request, account,
                       f'{label}-users-{account["username"]}') as ui:
            base = dataset['base_url']

            ui.goto('/users')
            ui.expect_flash(r'Admins only', kind='danger')
            assert ui.page.url.rstrip('/') == base.rstrip('/'), (
                f'{account["username"]} reached the Users page: {ui.page.url}')

            # Creating an account is a POST on the same page.
            response = ui.page.request.post(
                f'{base}/users/create',
                form={'full_name': 'Injected', 'username': 'pw_injected',
                      'email': 'pw_injected@example.test', 'password': 'Injected#2026',
                      'profile': 'Project Owner', 'cin': 'X1', 'phone': ''},
                max_redirects=0)
            assert response.status in (302, 303), (
                f'POST /users/create answered HTTP {response.status} for {account["username"]}')
            assert response.headers.get('location', '').rstrip('/') in ('', '/'), (
                'POST /users/create did not send the user back to the project list')
            ui.shot('users-refused')


@pytest.mark.end_to_end
def test_admin_sees_every_project_and_every_account(ui, dataset):
    ui.goto('/')
    ui.assert_no_server_error('the project list')
    names = listed_project_names(ui)
    missing = sorted({p['name'] for p in dataset['projects']} - set(names))
    assert not missing, f'the Admin does not see the seeded projects {missing}: {names!r}'
    ui.shot('admin-project-list')

    ui.goto('/users')
    ui.assert_no_danger('the Users page')
    body = ui.body_text()

    accounts = [dataset['admin']] + dataset['project_admins'] + dataset['owners']
    missing = [a['username'] for a in accounts if a['username'] not in body]
    assert not missing, f'the Users table is missing {missing}'
    assert 'pw_injected' not in body, 'a refused account was created anyway'

    # A local admin row carries its company agreement.
    admin = dataset['project_admins'][0]
    assert admin['company'] in body, 'the company name is not in the Users table'
    assert 'AGR-001' in body, 'the agreement number is not in the Users table'
    ui.shot('admin-users-list')


@pytest.mark.end_to_end
def test_local_admin_links_owner_so_owner_sees_the_project(browser, dataset, request):
    """A project a Project Admin creates for a landowner must reach that owner.

    The Project Admin creates the project, so ``user_id`` is theirs; the owner
    is linked on the create form.  The owner has to find the project in their
    own list, and saving the settings must keep the link.
    """
    local_admin = dataset['project_admins'][0]
    owner = local_admin['owners'][2]
    name = f'PW Linked {local_admin["username"]} {owner["username"]}'
    base = dataset['base_url'].rstrip('/')

    with signed_in(browser, dataset, request, local_admin,
                   f'localadmin-{local_admin["username"]}-linked-create') as ui:
        post_from_page(ui, '/create', {
            'name': name,
            'location': 'Linked plot',
            'admin_user_id': str(local_admin['id']),
            'owner_user_id': str(owner['id']),
        })
        ui.expect_flash(r'Project created', kind='success')
        ui.assert_no_server_error('creating a project for an owner')
        project_id = ui.page.url.replace(base, '').strip('/')
        assert project_id.isdigit(), f'the new project page is {ui.page.url}'

        # Saving the settings again keeps the owner linked.
        post_from_page(ui, f'/{project_id}/settings',
                       {'location': 'Linked plot', 'owner_user_id': str(owner['id'])})
        ui.expect_flash(r'settings saved', kind='success')
        ui.assert_no_server_error('saving the settings of a linked project')

    with signed_in(browser, dataset, request, owner,
                   f'owner-{owner["username"]}-linked-list') as ui:
        ui.goto('/')
        ui.assert_no_danger('the project list')
        names = listed_project_names(ui)
        assert name in names, (
            f'{owner["username"]} does not see the project linked to their '
            f'account, only {names!r}')
        ui.goto(f'/{project_id}')
        ui.assert_no_danger('the linked project')
        assert 'Project not found' not in ui.body_text()
        ui.shot('owner-linked-project')

    # The link grants access to that owner only: another customer still sees
    # only their own projects.
    other = local_admin['owners'][0]
    with signed_in(browser, dataset, request, other,
                   f'owner-{other["username"]}-not-linked') as ui:
        ui.goto('/')
        assert name not in listed_project_names(ui), (
            f'{other["username"]} sees a project linked to {owner["username"]}')

    # The suite shares the application database: leave it as it was found.
    with signed_in(browser, dataset, request, local_admin,
                   f'localadmin-{local_admin["username"]}-linked-cleanup') as ui:
        post_from_page(ui, f'/{project_id}/delete', {})
        ui.expect_flash(r'deleted', kind='info', timeout=15_000)
