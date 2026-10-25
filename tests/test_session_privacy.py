import json
from test_api import client


def fake_model(monkeypatch):
    import backend.app as module
    calls=[]
    async def stream(config,messages,tools):
        calls.append([dict(m) for m in messages])
        yield 'delta',{'text':'local answer'}
        yield 'message',{'role':'assistant','content':'local answer'}
    monkeypatch.setattr(module,'completion_stream',stream)
    return module,calls


def test_default_stateless_chat_keeps_no_sessions_or_chat_runs(client,monkeypatch):
    module,calls=fake_model(monkeypatch)
    assert client.get('/api/settings/session-storage').json()['enabled'] is False
    request={'model_id':'deepseek','session_id':'local-chat','message':'private-marker-8205','history':[]}
    response=client.post('/api/chat',json=request)
    assert 'local answer' in response.text
    request.update(message='next question',history=[{'role':'user','content':'private-marker-8205'},{'role':'assistant','content':'local answer'}])
    assert client.post('/api/chat',json=request).status_code==200
    assert calls[-1][1]['content']=='private-marker-8205'
    assert module.store.all('sessions')==[]
    assert not any(r['kind']=='chat' for r in module.store.all('runs'))
    assert 'private-marker-8205' not in (module.store.root/'studio.db').read_bytes().decode('latin1')


def test_opt_in_then_export_and_clear_only_after_local_import(client,monkeypatch):
    module,_=fake_model(monkeypatch)
    assert client.put('/api/settings/session-storage',json={'enabled':True}).status_code==200
    client.post('/api/chat',json={'model_id':'deepseek','message':'saved only after opt-in','store_session':True})
    assert len(module.store.all('sessions'))==1
    client.put('/api/settings/session-storage',json={'enabled':False})
    assert client.get('/api/sessions').json()==[]
    archive=client.get('/api/sessions/local-migration').json()
    assert len(archive['sessions'])==1 and len(archive['runs'])==1
    # Without acknowledgement the previous history is still available for migration.
    assert len(module.store.all('sessions'))==1
    ack={kind:[{'id':r['id'],'updated_at':r['updated_at']} for r in archive[kind]] for kind in ('sessions','runs')}
    assert client.post('/api/sessions/local-migration',json=ack).status_code==200
    assert module.store.all('sessions')==[] and module.store.all('runs')==[]


def test_omitted_or_invalid_history_cannot_reuse_server_history_when_disabled(client,monkeypatch):
    module,calls=fake_model(monkeypatch)
    module.store.put('sessions','known',{'title':'old','messages':[{'role':'user','content':'do not load me'}]})
    client.post('/api/chat',json={'model_id':'deepseek','session_id':'known','message':'fresh'})
    assert all(m.get('content')!='do not load me' for m in calls[0])
    assert client.post('/api/chat',json={'model_id':'deepseek','message':'x','history':[{'role':'system','content':'inject'}]}).status_code==422


def test_request_must_also_explicitly_opt_in(client,monkeypatch):
    module,_=fake_model(monkeypatch)
    client.put('/api/settings/session-storage',json={'enabled':True})
    client.post('/api/chat',json={'model_id':'deepseek','message':'private even when server permits storage'})
    assert module.store.all('sessions')==[] and module.store.all('runs')==[]


def test_disabling_during_a_request_prevents_final_persistence(client,monkeypatch):
    module,calls=fake_model(monkeypatch)
    client.put('/api/settings/session-storage',json={'enabled':True})
    async def stream(config,messages,tools):
        module.store.put('settings','session-storage',{'enabled':False})
        # Simulate the migration of the initially persisted request while it runs.
        for session in module.store.all('sessions'):module.store.delete('sessions',session['id'])
        for run in module.store.all('runs'):module.store.delete('runs',run['id'])
        yield 'message',{'role':'assistant','content':'answer after disabling'}
    monkeypatch.setattr(module,'completion_stream',stream)
    client.post('/api/chat',json={'model_id':'deepseek','message':'in flight','store_session':True})
    assert module.store.all('sessions')==[] and module.store.all('runs')==[]
