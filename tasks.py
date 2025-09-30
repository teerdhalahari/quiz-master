from celery_app import celery, db
from models import User, Quiz, Score
from datetime import datetime, timedelta
import csv
import io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import os
import requests
from dotenv import load_dotenv


load_dotenv()


SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
SMTP_USERNAME = os.environ.get('SMTP_USERNAME')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'noreply@quizmaster.com')


GCHAT_WEBHOOK_URL = os.environ.get('GCHAT_WEBHOOK_URL')

def send_email(to_email, subject, body, attachment=None):
    """Send email with optional attachment"""
    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD]):
        print("Email configuration missing. Skipping email send.")
        return False
        
    msg = MIMEMultipart()
    msg['From'] = FROM_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    
    msg.attach(MIMEText(body, 'html'))
    
    if attachment:
        part = MIMEApplication(attachment, Name='quiz_results.csv')
        part['Content-Disposition'] = f'attachment; filename="quiz_results.csv"'
        msg.attach(part)
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def send_gchat_notification(message):
    """Send notification to Google Chat"""
    if not GCHAT_WEBHOOK_URL:
        print("Google Chat webhook URL not configured. Skipping notification.")
        return False
        
    try:
        response = requests.post(
            GCHAT_WEBHOOK_URL,
            json={'text': message}
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error sending Google Chat notification: {e}")
        return False



@celery.task
def send_daily_reminders():
    """Send daily reminders to inactive users about new quizzes"""
    inactive_threshold = datetime.utcnow() - timedelta(days=7)
    inactive_users = User.query.filter(
        User.last_login < inactive_threshold,
        User.is_active == True
    ).all()
    
    new_quizzes = Quiz.query.filter(
        Quiz.created_at >= inactive_threshold,
        Quiz.is_active == True
    ).all()
    
    if not new_quizzes:
        return "No new quizzes to notify about"
    
    quiz_list = "\n".join([f"- {quiz.title}" for quiz in new_quizzes])
    
    for user in inactive_users:
        subject = "New Quizzes Available!"
        body = f"""
        <h2>Welcome back to Quiz Master!</h2>
        <p>We noticed you haven't been active lately. Here are some new quizzes you might be interested in:</p>
        {quiz_list}
        <p>Log in to your account to start taking these quizzes!</p>
        """
        
        send_email(user.email, subject, body)
        
        message = f"Daily reminder sent to {user.email} about {len(new_quizzes)} new quizzes"
        send_gchat_notification(message)
    
    return f"Sent reminders to {len(inactive_users)} inactive users"





@celery.task
def generate_monthly_reports():
    """Generate and send monthly activity reports to users"""
    users = User.query.filter_by(is_active=True).all()
    
    for user in users:
        last_month = datetime.utcnow() - timedelta(days=30)
        scores = Score.query.filter(
            Score.user_id == user.id,
            Score.completed_at >= last_month
        ).all()
        
        if not scores:
            continue
        

        total_quizzes = len(scores)
        passed_quizzes = sum(1 for score in scores if score.passed)
        avg_score = sum(score.score for score in scores) / total_quizzes
        avg_time = sum(score.time_taken for score in scores) / total_quizzes
        
        all_scores = Score.query.filter(Score.completed_at >= last_month).all()
        user_scores = {score.user_id: score.score for score in all_scores}
        sorted_users = sorted(user_scores.items(), key=lambda x: x[1], reverse=True)
        user_rank = next(i for i, (uid, _) in enumerate(sorted_users, 1) if uid == user.id)
        
        table_rows = []
        for score in scores[:5]:
            row = f"""
            <tr>
                <td>{score.quiz.title}</td>
                <td>{score.score:.1f}%</td>
                <td>{score.time_taken/60:.1f} minutes</td>
                <td>{'Passed' if score.passed else 'Failed'}</td>
            </tr>"""
            table_rows.append(row)
        
        subject = "Your Monthly Quiz Master Report"
        body = f"""
        <h2>Monthly Activity Report</h2>
        <p>Hello {user.first_name or user.username},</p>
        <p>Here's your monthly activity report:</p>
        <ul>
            <li>Quizzes Completed: {total_quizzes}</li>
            <li>Quizzes Passed: {passed_quizzes}</li>
            <li>Average Score: {avg_score:.1f}%</li>
            <li>Average Time per Quiz: {avg_time/60:.1f} minutes</li>
            <li>Your Ranking: #{user_rank}</li>
        </ul>
        <h3>Recent Quiz Results:</h3>
        <table border="1">
            <tr>
                <th>Quiz</th>
                <th>Score</th>
                <th>Time Taken</th>
                <th>Status</th>
            </tr>
            {''.join(table_rows)}
        </table>
        """
        
        send_email(user.email, subject, body)
        

        message = f"Monthly report sent to {user.email}"
        send_gchat_notification(message)
    
    return f"Generated and sent reports to {len(users)} users"

@celery.task
def export_user_quizzes_as_csv(user_id):
    """Export a user's quiz attempts as CSV"""
    user = User.query.get_or_404(user_id)
    scores = Score.query.filter_by(user_id=user_id).order_by(Score.completed_at.desc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        'Quiz Title', 'Subject', 'Chapter', 'Score', 'Time Taken (minutes)',
        'Passed', 'Completed At'
    ])
    
    for score in scores:
        writer.writerow([
            score.quiz.title,
            score.quiz.chapter.subject.name,
            score.quiz.chapter.name,
            f"{score.score:.1f}%",
            f"{score.time_taken/60:.1f}",
            'Yes' if score.passed else 'No',
            score.completed_at.strftime('%Y-%m-%d %H:%M:%S')
        ])
    
    csv_data = output.getvalue()


    subject = "Your Quiz Results Export"
    body = f"""
    <h2>Quiz Results Export</h2>
    <p>Hello {user.first_name or user.username},</p>
    <p>Please find your quiz results attached to this email.</p>
    """
    
    send_email(user.email, subject, body, csv_data.encode())
    
    # g chat notification
    message = f"Quiz results export sent to {user.email}"
    send_gchat_notification(message)
    
    return f"Exported {len(scores)} quiz attempts for user {user.email}" 




