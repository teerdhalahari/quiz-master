from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from config import Config
from database import db
from celery_app import create_celery_app
from celery_config import beat_schedule, task_routes, task_time_limit, task_soft_time_limit
from routes import api_bp
from models import User, Subject, Chapter, Quiz, Question, Choice, Score, Answer, UserAnswer
import logging
import redis
import os


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    CORS(app, 
         resources={r"/*": {
             "origins": "*",
             "methods": ["GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"],
             "allow_headers": ["Content-Type", "Authorization", "Accept", "Origin", "X-Requested-With"]
         }},
         supports_credentials=True)
    
    jwt = JWTManager(app)
    db.init_app(app)
    migrate = Migrate(app, db)
    celery = create_celery_app(app)
    
    celery.conf.update(
        beat_schedule=beat_schedule,
        task_routes=task_routes,
        task_time_limit=task_time_limit,
        task_soft_time_limit=task_soft_time_limit
    )
    
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)
    
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.DEBUG)
    
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    try:
        redis_client = redis.from_url(redis_url)
        redis_client.ping()
        logger.info("Successfully connected to Redis")
    except redis.exceptions.ConnectionError:
        logger.warning("Failed to connect to Redis. Caching will be disabled.")
    
    app.register_blueprint(api_bp, url_prefix='/api')

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000) 