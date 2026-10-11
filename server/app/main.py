"""Run: uvicorn server.app.main:app --host 127.0.0.1 --port 8000."""
import json
import logging
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from .feedback import me_card, speech_card
from .leaderboard import UNNAMED, NameTaken, clean_name, rank
from .questions import DIFFICULTIES, UnsuitableCategory, clean_category, generate_question
from .settings import ROOT, Settings

LOG = logging.getLogger(__name__)
LOOPBACK = {'localhost', '127.0.0.1', '::1'}
MIME_SUFFIX = {'audio/wav': '.wav', 'audio/x-wav': '.wav', 'audio/webm': '.webm', 'video/webm': '.webm',
               'audio/ogg': '.ogg', 'audio/mp4': '.mp4', 'audio/mpeg': '.mp3', 'audio/flac': '.flac'}


def real_grade(path, prompt, side, cache_dir):
    grader_dir = str(ROOT / 'softskills-grader')
    if grader_dir not in sys.path:
        sys.path.insert(0, grader_dir)
    from grader.pipeline import grade
    return grade(path, prompt, side, cache_dir=cache_dir)


class QuestionRequest(BaseModel):
    category: str
    difficulty: str


class NameRequest(BaseModel):
    display_name: str


def create_app(settings=None, repository=None, grader=None, question_maker=None):
    settings = settings or Settings.from_env()
    app = FastAPI(title='SpeakUp Solo API')
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=['GET', 'POST'], allow_headers=['Authorization', 'Content-Type', 'X-Demo-Mode'])
    app.state.repository = repository

    @app.middleware('http')
    async def local_demo_only(request, call_next):
        # Demo and local modes have no accounts, so they only answer this computer.
        if settings.demo_mode or settings.local_mode:
            origin = request.headers.get('origin')
            if request.url.hostname not in LOOPBACK or (request.client and request.client.host not in LOOPBACK) or (origin and urlparse(origin).hostname not in LOOPBACK):
                return Response('Demo and local modes are restricted to loopback.', status_code=403)
        response = await call_next(request)
        if settings.demo_mode:
            response.headers['X-Demo-Mode'] = 'true'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        elif settings.demo_mode or settings.local_mode:
            # Local play serves the working copy: always revalidate, so edits show up on reload.
            response.headers['Cache-Control'] = 'no-cache'
        return response

    def repo():
        if app.state.repository is None:
            if settings.demo_mode:
                from .demo import DemoRepository
                app.state.repository = DemoRepository(settings)
            elif settings.local_mode:
                from .local import LocalRepository
                app.state.repository = LocalRepository(settings)
            else:
                if not settings.supabase_url or not settings.supabase_service_role_key:
                    raise HTTPException(503, 'Server is not configured. Set Supabase server credentials.')
                from .repository import SupabaseRepository
                app.state.repository = SupabaseRepository(settings)
        return app.state.repository

    def user(authorization: str | None = Header(default=None), db=Depends(repo)):
        if not authorization or not authorization.startswith('Bearer ') or not authorization[7:]:
            raise HTTPException(401, 'Sign in to continue.', headers={'WWW-Authenticate': 'Bearer'})
        try:
            return db.resolve_token(authorization[7:])
        except Exception:
            raise HTTPException(401, 'Your session expired. Sign in again.', headers={'WWW-Authenticate': 'Bearer'})

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        # Avoid returning provider errors or credentials to the browser.
        LOG.error('API operation failed (%s)', type(exc).__name__)
        return Response(json.dumps({'detail': 'Service unavailable. Try again.'}), status_code=503, media_type='application/json')

    @app.get('/config.js')
    def config():
        accountless = settings.demo_mode or settings.local_mode
        public = dict(API_URL='', SUPABASE_URL='' if accountless else settings.supabase_url,
                      SUPABASE_ANON_KEY='' if accountless else settings.supabase_anon_key,
                      DEMO_MODE=settings.demo_mode, LOCAL_MODE=settings.local_mode and not settings.demo_mode)
        return Response('window.ARENA_CONFIG = ' + json.dumps(public) + ';', media_type='application/javascript', headers={'Cache-Control': 'no-store'})

    @app.get('/api/prompts')
    def prompts(uid=Depends(user), db=Depends(repo)):
        return db.prompts()

    @app.post('/api/questions')
    def question(body: QuestionRequest, uid=Depends(user), db=Depends(repo)):
        if settings.demo_mode:
            raise HTTPException(503, 'Local demo contains saved examples only. Configure live grading to play.')
        if body.difficulty not in DIFFICULTIES:
            raise HTTPException(422, 'Difficulty must be beginner, intermediate or expert.')
        try:
            category = clean_category(body.category)
            text = (question_maker or generate_question)(category, body.difficulty)
        except UnsuitableCategory as error:
            raise HTTPException(422, str(error))
        except ValueError as error:
            raise HTTPException(422, str(error))
        motion_id = db.create_motion(text, category=category, difficulty=body.difficulty)
        return {'id': motion_id, 'text': text, 'category': category, 'difficulty': body.difficulty}

    @app.get('/api/me')
    def me(uid=Depends(user), db=Depends(repo)):
        result = me_card(db.profile(uid), db.speeches(uid), db.prompts())
        if settings.demo_mode:
            result['demo'] = True
        return result

    @app.post('/api/me/name')
    def name(body: NameRequest, uid=Depends(user), db=Depends(repo)):
        if settings.demo_mode:
            raise HTTPException(503, 'Local demo contains saved examples only. Configure live grading to play.')
        try:
            value = clean_name(body.display_name)
            db.set_display_name(uid, value)
        except ValueError as error:
            raise HTTPException(422, str(error))
        except NameTaken:
            raise HTTPException(409, 'That name is taken. Try another one.')
        return {'display_name': value}

    @app.get('/api/leaderboard')
    def leaderboard(uid=Depends(user), db=Depends(repo)):
        display_name = db.profile(uid)['display_name']
        result = rank(db.board_rows(), uid)
        result['display_name'] = None if display_name == UNNAMED else display_name
        if settings.demo_mode:
            result['demo'] = True
        return result

    @app.get('/api/speeches/{speech_id}')
    def card(speech_id: str, uid=Depends(user), db=Depends(repo)):
        row = db.speech(speech_id, uid)
        if row is None:
            raise HTTPException(404, 'Speech not found.')
        prompt = next((p for p in db.prompts() if p['id'] == row['motion_id']), {'id': row['motion_id'], 'text': row.get('prompt_text', 'Archived prompt')})
        result = speech_card(row, db.speeches(uid), {key: prompt[key] for key in ('id', 'text')}, db.signed_audio(row['audio_path']) if row.get('audio_path') else None)
        if settings.demo_mode:
            result['demo'] = True
        return result

    def grade_background(db, id, data, suffix, prompt, side):
        try:
            db.update_speech(id, {'status': 'grading'})
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=settings.data_dir) as directory:
                path = Path(directory) / ('recording' + suffix)
                path.write_bytes(data)
                report = (grader or real_grade)(str(path), prompt, side, str(settings.data_dir / 'grader-cache'))
            valid = report.get('gradable') is True
            db.update_speech(id, dict(status='done' if valid else 'ungradable', report=report,
                                     overall=report['overall']['score'] if valid else None,
                                     standards_id=report.get('inputs', {}).get('standards_id'), error=None))
        except Exception as exc:
            LOG.error('Grading failed for %s (%s)', id, type(exc).__name__)
            db.update_speech(id, {'status': 'failed', 'error': 'Grading failed. Please record again.'})

    @app.post('/api/speeches', status_code=202)
    async def upload(background: BackgroundTasks, motion_id: str = Form(...), side: str = Form(...), audio: UploadFile = File(...), uid=Depends(user), db=Depends(repo)):
        if settings.demo_mode:
            raise HTTPException(503, 'Local demo contains saved examples only. Configure live grading to upload recordings.')
        if side not in ('yes', 'no'):
            raise HTTPException(422, 'Side must be yes or no.')
        prompt = next((p for p in db.prompts() if p['id'] == motion_id), None)
        if prompt is None:
            raise HTTPException(404, 'Prompt not found.')
        mime = (audio.content_type or '').split(';')[0].lower()
        suffix = MIME_SUFFIX.get(mime)
        if not suffix:
            raise HTTPException(415, 'Upload WAV, WebM, Ogg, MP4, MP3, or FLAC audio.')
        data = await audio.read(settings.max_audio_bytes + 1)
        await audio.close()
        if not data:
            raise HTTPException(422, 'The recording is empty.')
        if len(data) > settings.max_audio_bytes:
            raise HTTPException(413, 'The recording exceeds 15 MB.')
        db.profile(uid)
        id = str(uuid4())
        path = f'{uid}/{id}{suffix}'
        db.upload(path, data, mime)
        try:
            db.create_speech(dict(id=id, player_id=uid, motion_id=motion_id, side=side, audio_path=path, status='queued'))
        except Exception:
            if hasattr(db, 'remove_audio'):
                db.remove_audio(path)
            raise
        background.add_task(grade_background, db, id, data, suffix, prompt['text'], side)
        return {'speech_id': id}

    if settings.local_mode and not settings.demo_mode:
        @app.get('/api/local/audio/{path:path}')
        def local_audio(path: str, db=Depends(repo)):
            # The <audio> element cannot send a bearer token; local mode is loopback-only.
            target = db.audio_file(path)
            if target is None:
                raise HTTPException(404, 'Recording not found.')
            return FileResponse(target)

    if settings.demo_mode:
        @app.get('/api/demo/audio')
        def demo_audio(db=Depends(repo)):
            return FileResponse(db.audio, media_type='audio/wav', headers={'X-Demo-Mode': 'true'})

    app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
    return app


app = create_app()
