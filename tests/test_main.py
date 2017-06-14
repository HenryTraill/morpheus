import base64
import hashlib
import hmac
import json
import uuid


async def test_index(cli):
    r = await cli.get('/')
    assert r.status == 200
    assert 'Morpheus - The Greek God' in await r.text()


async def test_index_head(cli):
    r = await cli.head('/')
    assert r.status == 200
    assert '' == await r.text()


async def test_robots(cli):
    r = await cli.get('/robots.txt')
    assert r.status == 200
    assert 'User-agent: *' in await r.text()


async def test_favicon(cli):
    r = await cli.get('/favicon.ico', allow_redirects=False)
    assert r.status == 200
    assert 'image' in r.headers['Content-Type']  # value can vary


async def test_send_message(cli, tmpdir):
    data = {
        'uid': 'x' * 20,
        'company_code': 'foobar',
        'from_address': 'Samuel <s@muelcolvin.com>',
        'method': 'email-test',
        'subject_template': 'test email {{ a }}',
        'context': {
            'message__render': '# hello\n\nThis is a **{{ b }}**.\n',
            'a': 'Apple',
            'b': f'Banana',
        },
        'recipients': [
            {
                'first_name': 'foo',
                'last_name': f'bar',
                'address': f'foobar@example.com',
            }
        ]
    }
    r = await cli.post('/send/', json=data, headers={'Authorization': 'testing-key'})
    assert r.status == 201, await r.text()
    assert len(tmpdir.listdir()) == 1
    msg_file = tmpdir.join('xxxxxxxxxxxxxxxxxxxx-foobarexamplecom.txt').read()
    print(msg_file)
    assert '\nsubject: test email Apple\n' in msg_file
    assert '\n<p>This is a <strong>Banana</strong>.</p>\n' in msg_file
    assert '"from_email": "s@muelcolvin.com",\n' in msg_file
    assert '"to_email": "foobar@example.com",\n' in msg_file


async def test_webhook(cli, send_message):
    message_id = await send_message(uid='x' * 20)
    r = await cli.server.app['es'].get('messages/email-test/xxxxxxxxxxxxxxxxxxxx-foobartestingcom')
    data = await r.json()
    assert data['_source']['status'] == 'send'
    first_update_ts = data['_source']['update_ts']
    assert data['_source']['send_ts'] == first_update_ts
    assert len(data['_source']['events']) == 0
    data = {
        'ts': int(1e10),
        'event': 'open',
        '_id': message_id,
        'foobar': ['hello', 'world']
    }
    r = await cli.post('/webhook/test/', json=data)
    assert r.status == 200, await r.text()
    r = await cli.server.app['es'].get('messages/email-test/xxxxxxxxxxxxxxxxxxxx-foobartestingcom')
    data = await r.json()
    assert data['_source']['status'] == 'open'
    assert len(data['_source']['events']) == 1
    assert data['_source']['update_ts'] > first_update_ts


async def test_mandrill_send(cli, send_message):
    r = await cli.server.app['es'].get('messages/email-mandrill/mandrill-foobartestingcom', allowed_statuses='*')
    assert r.status == 404, await r.text()
    await send_message(method='email-mandrill')

    r = await cli.server.app['es'].get('messages/email-mandrill/mandrill-foobartestingcom', allowed_statuses='*')
    assert r.status == 200, await r.text()
    data = await r.json()
    assert data['_source']['to_email'] == 'foobar@testing.com'


async def test_mandrill_webhook(cli):
    await cli.server.app['es'].post(
        f'messages/email-mandrill/test-webhook',
        company='foobar',
        send_ts=123,
        update_ts=123,
        status='send',
        to_email='testing@example.com',
        events=[]
    )
    r = await cli.server.app['es'].get('messages/email-mandrill/test-webhook')
    assert r.status == 200
    data = await r.json()
    assert len(data['_source']['events']) == 0
    messages = [{'ts': int(1e10), 'event': 'open', '_id': 'test-webhook', 'foobar': ['hello', 'world']}]
    data = {'mandrill_events': json.dumps(messages)}

    sig = base64.b64encode(
        hmac.new(
            b'testing',
            msg=(b'https://None/webhook/mandrill/mandrill_events[{"ts": 10000000000, '
                 b'"event": "open", "_id": "test-webhook", "foobar": ["hello", "world"]}]'),
            digestmod=hashlib.sha1
        ).digest()
    )
    r = await cli.post('/webhook/mandrill/', data=data, headers={'X-Mandrill-Signature': sig.decode()})
    assert r.status == 200, await r.text()
    r = await cli.server.app['es'].get('messages/email-mandrill/test-webhook')
    assert r.status == 200
    data = await r.json()
    assert len(data['_source']['events']) == 1
    assert data['_source']['update_ts'] == 1e13
    assert data['_source']['status'] == 'open'


async def test_send_message_headers(cli, tmpdir):
    uid = str(uuid.uuid4())
    data = {
        'uid': uid,
        'company_code': 'foobar',
        'from_address': 'Samuel <s@muelcolvin.com>',
        'method': 'email-test',
        'subject_template': 'test email {{ a }}',
        'context': {
            'message__render': 'test email {{ a }} {{ b}} {{ c }}.\n',
            'a': 'Apple',
            'b': f'Banana',
        },
        'headers': {
            'Reply-To': 'another@whoever.com',
            'List-Unsubscribe': '<http://example.com/unsub>'
        },
        'recipients': [
            {
                'first_name': 'foo',
                'last_name': f'bar',
                'address': f'foobar@example.com',
                'context': {
                    'c': 'Carrot',
                },
            },
            {
                'address': f'2@example.com',
                'context': {
                    'b': 'Banker',
                },
                'headers': {
                    'List-Unsubscribe': '<http://example.com/different>'
                },
            }
        ]
    }
    r = await cli.post('/send/', json=data, headers={'Authorization': 'testing-key'})
    assert r.status == 201, await r.text()
    assert len(tmpdir.listdir()) == 2
    msg_file = tmpdir.join(f'{uid}-foobarexamplecom.txt').read()
    # print(msg_file)
    assert '<p>test email Apple Banana Carrot.</p>\n' in msg_file
    assert '"to_email": "foobar@example.com",\n' in msg_file
    assert '"Reply-To": "another@whoever.com",\n' in msg_file
    assert '"List-Unsubscribe": "<http://example.com/unsub>"\n' in msg_file

    msg_file = tmpdir.join(f'{uid}-2examplecom.txt').read()
    print(msg_file)
    assert '<p>test email Apple Banker .</p>\n' in msg_file
    assert '"to_email": "2@example.com",\n' in msg_file
    assert '"Reply-To": "another@whoever.com",\n' in msg_file
    assert '"List-Unsubscribe": "<http://example.com/different>"\n' in msg_file


async def test_send_unsub_context(send_message, tmpdir):
    uid = str(uuid.uuid4())
    await send_message(
        uid=uid,
        context={
            'message__render': 'test email {{ unsubscribe_link }}.\n',
            'unsubscribe_link': 'http://example.com/unsub'
        },
        recipients=[
            {'address': f'1@example.com'},
            {
                'address': f'2@example.com',
                'context': {
                    'unsubscribe_link': 'http://example.com/context'
                },
                'headers': {
                    'List-Unsubscribe': '<http://example.com/different>'
                },
            }
        ]
    )
    assert len(tmpdir.listdir()) == 2
    msg_file = tmpdir.join(f'{uid}-1examplecom.txt').read()
    # print(msg_file)
    assert '"to_email": "1@example.com",\n' in msg_file
    assert '"List-Unsubscribe": "<http://example.com/unsub>"\n' in msg_file
    assert '<p>test email http://example.com/unsub.</p>\n' in msg_file

    msg_file = tmpdir.join(f'{uid}-2examplecom.txt').read()
    print(msg_file)
    assert '"to_email": "2@example.com",\n' in msg_file
    assert '"List-Unsubscribe": "<http://example.com/different>"\n' in msg_file
    assert '<p>test email http://example.com/context.</p>\n' in msg_file


async def test_markdown_context(send_message, tmpdir):
    message_id = await send_message(
        main_template='testing {{{ foobar }}}',
        context={
            'message__render': 'test email {{ unsubscribe_link }}.\n',
            'foobar__md': '[hello](www.example.com/hello)'
        },
    )
    assert len(tmpdir.listdir()) == 1
    msg_file = tmpdir.join(f'{message_id}.txt').read()
    print(msg_file)
    assert 'content:\ntesting <p><a href="www.example.com/hello">hello</a></p>\n' in msg_file


async def test_partials(send_message, tmpdir):
    message_id = await send_message(
        main_template=('message: |{{{ message }}}|\n'
                       'foo: {{ foo }}\n'
                       'partial: {{> test_p }}'),
        context={
            'message__render': '{{foo}} {{> test_p }}',
            'foo': 'FOO',
            'bar': 'BAR',
        },
        mustache_partials={
            'test_p': 'foo ({{ foo }}) bar **{{ bar }}**',
        }
    )
    assert len(tmpdir.listdir()) == 1
    msg_file = tmpdir.join(f'{message_id}.txt').read()
    print(msg_file)
    assert """
content:
message: |<p>FOO foo (FOO) bar <strong>BAR</strong></p>
|
foo: FOO
partial: foo (FOO) bar **BAR**
""" in msg_file


async def test_macros(send_message, tmpdir):
    message_id = await send_message(
        main_template='macro result: {{ foobar(hello, {{ foo }}) }}',
        context={
            'foo': 'FOO',
            'bar': 'BAR',
        },
        macros={
            'foobar(a, b)': '___{{ a }} {{b}}___'
        }
    )
    assert len(tmpdir.listdir()) == 1
    msg_file = tmpdir.join(f'{message_id}.txt').read()
    print(msg_file)
    assert 'content:\nmacro result: ___hello FOO___\n' in msg_file


async def test_macros_more(send_message, tmpdir):
    message_id = await send_message(
        main_template=(
            'foo:{{ foo() }}\n'
            'foo wrong:{{ foo(1, 2) }}\n'
            'bar:{{ bar() }}\n'
            'spam1:{{ spam(x, y ) }}\n'
            'spam2:{{ spam(with bracket ) , {{ bar}} ) }}\n'
            'spam3:{{ spam({{ foo }}, {{ bar}} ) }}\n'
            'spam wrong: {{ spam(1, {{ bar}}, x) }}\n'
        ),
        context={
            'foo': 'FOO',
            'bar': 'BAR',
        },
        macros={
            'foo()': '___is foo___',
            'bar': '___is bar___',
            'spam(apple, pear)': '___spam {{apple}} {{pear}}___',
        }
    )
    assert len(tmpdir.listdir()) == 1
    msg_file = tmpdir.join(f'{message_id}.txt').read()
    print(msg_file)
    assert """
content:
foo:___is foo___
foo wrong:
bar:
spam1:___spam x y___
spam2:___spam with bracket ) BAR___
spam3:___spam FOO BAR___
spam wrong: , x) }}
""" in msg_file
