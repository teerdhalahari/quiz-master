from celery.schedules import crontab
import os

broker_url = 'redis://localhost:6379/0'
result_backend = 'redis://localhost:6379/0'

task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json']
timezone = 'UTC'
enable_utc = True

task_time_limit = 3600
task_soft_time_limit = 3000

task_routes = {
    'tasks.send_daily_reminders': {'queue': 'reminders'},
    'tasks.generate_monthly_reports': {'queue': 'reports'},
    'tasks.export_user_quizzes_as_csv': {'queue': 'exports'}
}

task_annotations = {
    'tasks.send_daily_reminders': {'rate_limit': '10/m'},
    'tasks.generate_monthly_reports': {'rate_limit': '1/h'},
    'tasks.export_user_quizzes_as_csv': {'rate_limit': '5/m'}
}

beat_schedule = {
    'send-daily-reminders': {
        'task': 'tasks.send_daily_reminders',
        'schedule': crontab(hour=18, minute=0)  # Run at 6:00 PM daily
    },
    'generate-monthly-reports': {
        'task': 'tasks.generate_monthly_reports',
        'schedule': crontab(0, 0, day_of_month='1')  # Run on the 1st of each month
    }
}

worker_max_tasks_per_child = 100
worker_prefetch_multiplier = 1

result_expires = 3600  
task_track_started = True  


