It is a multi-user app that acts as an exam preparation site for multiple courses built with Flask, Vue.js, and SQLite.

Terminal 1 – Start Redis Server

redis-server

Terminal 2 – Start Flask Backend

cd backend
source venv311/bin/activate
flask run --host=127.0.0.1 --port=5000

Terminal 3 – Start Celery Worker

cd backend
source venv311/bin/activate
celery -A celery_app.celery worker --loglevel=info

Terminal 4 – Start Celery Beat (Scheduler)

cd backend
source venv311/bin/activate
celery -A celery_app.celery beat --loglevel=info

Terminal 5 – Start Frontend (Vue)

cd frontend
npm run dev
