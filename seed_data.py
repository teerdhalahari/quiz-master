#!/usr/bin/env python

from app import create_app
from models import db, User, Subject, Chapter, Quiz, Question, Choice, Score
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import random

app = create_app()
app.app_context().push()

def create_users():
    
    print("creating admin")
    admin = User.query.filter_by(email='admin@gmail.com').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@gmail.com',
            first_name='Admin',
            last_name='User',
            password_hash=generate_password_hash('Admin@123'),
            role='admin',
            is_active=True
        )
        db.session.add(admin)
        db.session.commit()
        print("admin user created successfully!")
    
    users = [
        {
            'username': 'student1',
            'email': 'student1@gmail.com',
            'password': 'Student1@123',
            'first_name': 'Santoshi',
            'last_name': 'Lakkoju',
            'qualification': 'Undergraduate',
            'dob': '2004-07-28'
        },
        {
            'username': 'student2',
            'email': 'student2@gmail.com',
            'password': 'Student2@123',
            'first_name': 'Balu',
            'last_name': 'Mullapudi',
            'qualification': 'Graduate',
            'dob': '1996-03-22'
        },
        {
            'username': 'student3',
            'email': 'student3@gmail.com',
            'password': 'Student@123',
            'first_name': 'Varmini',
            'last_name': 'Mikkilineni',
            'qualification': 'High School',
            'dob': '2000-11-07'
        },
    ]


    
    for user_data in users:
        if User.query.filter_by(email=user_data['email']).first():
            continue
            
        user = User(
            username=user_data['username'],
            email=user_data['email'],
            first_name=user_data['first_name'],
            last_name=user_data['last_name'],
            password_hash=generate_password_hash(user_data['password']),
            role='user',
            is_active=True
        )
        db.session.add(user)
    
    db.session.commit()
    print("users created successfully!")




def create_subjects():
    """Create 3 sample subjects"""
    print("creating sample subjects")
    
    subjects = [
        {
            'name': 'Mathematics',
            'description': 'Statistics, Trigonometry',
        },
        {
            'name': 'Physics',
            'description': 'Motion, Kinematics',
        },
        {
            'name': 'Chemistry',
            'description': 'Molecules, organic Chemistry',
        },
    ]
    
    created_subjects = []
    
    for subject_data in subjects:
        existing = Subject.query.filter_by(name=subject_data['name']).first()
        if existing:
            created_subjects.append(existing)
            continue
            
        subject = Subject(
            name=subject_data['name'],
            description=subject_data['description'],
        )
        db.session.add(subject)
        db.session.flush()
        created_subjects.append(subject)
    
    db.session.commit()
    print("subjects created successfully")
    
    return created_subjects





def create_chapters(subjects):
    """Create 3 chapters for each subject"""
    print("creating sample chapters")  
    created_chapters = []
    
    for subject in subjects:
        if Chapter.query.filter_by(subject_id=subject.id).count() >= 5:
            created_chapters.extend(Chapter.query.filter_by(subject_id=subject.id).all())
            continue
            
        for i in range(1, 4):
            chapter = Chapter(
                subject_id=subject.id,
                name=f"Chapter {i}: {subject.name} Basics {i}",
                description=f"Introduction to {subject.name} concepts - Part {i}",
                order=i
            )
            db.session.add(chapter)
            db.session.flush()
            created_chapters.append(chapter)
    
    db.session.commit()
    print("chapters created successfully")   
    return created_chapters



def create_quizzes(chapters):
    """Create 3 quizzes for each chapter"""
    print("creating sample quizzes")  
    created_quizzes = []
    now = datetime.utcnow()
    
    for chapter in chapters:
        if Quiz.query.filter_by(chapter_id=chapter.id).count() >= 3:
            created_quizzes.extend(Quiz.query.filter_by(chapter_id=chapter.id).all())
            continue
            
        for i in range(1, 4):
            quiz = Quiz(
                chapter_id=chapter.id,
                title=f"Quiz {i} - {chapter.name}",
                description=f"Test your {chapter.name}",
                duration_minutes=random.choice([15, 30, 45, 60]),
                passing_score=60,
                start_date=now - timedelta(days=i),
                end_date=now + timedelta(days=30),
                is_active=True
            )
            db.session.add(quiz)
            db.session.flush()
            created_quizzes.append(quiz)
    
    db.session.commit()
    print("quizzes created successfully")
    
    return created_quizzes



def create_questions(quizzes):
    """Create 3 questions for each quiz"""
    print("creating sample questions")
    
    for quiz in quizzes:
        if Question.query.filter_by(quiz_id=quiz.id).count() >= 3:
            continue
            
        for i in range(1, 4):
            question = Question(
                quiz_id=quiz.id,
                text=f"Question {i} about {quiz.title}?",
                explanation=f"Explanation for question {i}",
                points=random.choice([1, 2, 3, 5])
            )
            db.session.add(question)
            db.session.flush()
            
    
            correct_choice = random.randint(0, 3)
            for j in range(4):
                choice = Choice(
                    question_id=question.id,
                    text=f"Option {j+1} for question {i}",
                    is_correct=(j == correct_choice)
                )
                db.session.add(choice)
    
    db.session.commit()
    print("questions and options created successfully!")


def seed_all():
    """Run all seeding functions"""
    create_users()
    subjects = create_subjects()
    chapters = create_chapters(subjects)
    quizzes = create_quizzes(chapters)
    create_questions(quizzes)
    print("Database seeding completed")
if __name__ == "__main__":
    seed_all() 



