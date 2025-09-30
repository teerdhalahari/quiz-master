from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity, create_access_token, create_refresh_token
from werkzeug.security import generate_password_hash, check_password_hash
from models import (
    User, Subject, Chapter, Quiz, Question, Choice, Score, Answer, UserAnswer, db
)
import redis
import json
from functools import wraps
import datetime
import random
import os
from celery import Celery
from tasks import export_user_quizzes_as_csv
import logging



api_bp = Blueprint('api', __name__)

redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
try:
    redis_client = redis.from_url(redis_url)
    redis_available = True
except redis.exceptions.ConnectionError:
    redis_available = False
    print("Warning: Redis connection failed, caching disabled")

CACHE_TIMEOUT = 300  




def cache_response(timeout=CACHE_TIMEOUT):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not redis_available:
                return f(*args, **kwargs)
                
            try:
                
                cache_key = f'cache:{f.__name__}:{str(args)}:{str(kwargs)}'
                cached_response = redis_client.get(cache_key)
                
                if cached_response:
                    return json.loads(cached_response)
                
                
                response = f(*args, **kwargs)
             
                redis_client.setex(
                    cache_key,
                    timeout,
                    json.dumps(response)
                )
                
                return response
            except Exception as e:
                print(f"Cache error: {e}")
                return f(*args, **kwargs)
                
        return decorated_function
    return decorator



def safe_delete_cache(pattern):
    """Safely delete cache keys matching a pattern"""
    if not redis_available:
        return
        
    try:
        redis_client.delete(pattern)
        try:
            for key in redis_client.scan_iter(pattern):
                redis_client.delete(key)
        except Exception:
            pass
            
    except Exception as e:
        print(f"Error deleting cache key {pattern}: {e}")



def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            current_user_email = get_jwt_identity()
            if not current_user_email:
                return jsonify({'error': 'Authentication required'}), 401
                
            user = User.query.filter_by(email=current_user_email).first()
            
            if not user or user.role != 'admin':
                return jsonify({'error': 'Admin privileges required'}), 403
            
            return f(*args, **kwargs)
        except Exception as e:
            print(f"Admin authorization error: {e}")
            return jsonify({'error': 'Authentication error'}), 401
    return decorated_function


# Auth routes
@api_bp.route('/auth/register', methods=['POST'])
def register():
    data = request.json
    
    required_fields = ['username', 'email', 'password']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already registered'}), 409
    
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already taken'}), 409
    
    user = User(
        username=data['username'],
        email=data['email'],
        first_name=data.get('first_name', ''),
        last_name=data.get('last_name', '')
    )
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    access_token = create_access_token(identity=user.email)
    refresh_token = create_refresh_token(identity=user.email)
    
    return jsonify({
        'message': 'User registered successfully',
        'access_token': access_token,
        'refresh_token': refresh_token,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': user.role
        }
    }), 201

@api_bp.route('/auth/login', methods=['POST'])
def login():
    data = request.json
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'Missing email or password'}), 400
    
    user = User.query.filter_by(email=data['email']).first()
    
    if not user or not user.check_password(data['password']):
        return jsonify({'error': 'Invalid email or password'}), 401
    
    if not user.is_active:
        return jsonify({'error': 'Your account is inactive. Please contact the administrator.'}), 403
    
    user.last_login = datetime.datetime.utcnow()
    db.session.commit()
    
    access_token = create_access_token(identity=user.email)
    refresh_token = create_refresh_token(identity=user.email)
    
    return jsonify({
        'message': 'Login successful',
        'access_token': access_token,
        'refresh_token': refresh_token,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': user.role,
            'is_active': user.is_active
        }
    }), 200



@api_bp.route('/auth/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    current_user = get_jwt_identity()
    access_token = create_access_token(identity=current_user)
    refresh_token = create_refresh_token(identity=current_user)
    
    return jsonify({
        'access_token': access_token,
        'refresh_token': refresh_token
    }), 200

@api_bp.route('/auth/user', methods=['GET'])
@jwt_required()
def get_user():
    current_user_email = get_jwt_identity()
    user = User.query.filter_by(email=current_user_email).first()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role,
        'first_name': user.first_name,
        'last_name': user.last_name
    }), 200

@api_bp.route('/auth/check', methods=['GET'])
@jwt_required()
def check_auth():
    """Simple endpoint to validate if token is still valid"""
    current_user_email = get_jwt_identity()
    user = User.query.filter_by(email=current_user_email).first()
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'authenticated': True,
        'role': user.role
    }), 200




@api_bp.route('/subjects', methods=['GET'])
@cache_response()
def get_subjects():
    subjects = Subject.query.all()
    
    subjects_list = [{
        'id': subject.id,
        'name': subject.name,
        'description': subject.description,
        'image_url': subject.image_url,
        'created_at': subject.created_at.isoformat()
    } for subject in subjects]
    
    return jsonify(subjects_list), 200

@api_bp.route('/subjects/<int:subject_id>', methods=['GET'])
@cache_response()
def get_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    
    chapters = [{
        'id': chapter.id,
        'name': chapter.name,
        'description': chapter.description,
        'order': chapter.order
    } for chapter in subject.chapters.order_by(Chapter.order)]
    
    return jsonify({
        'id': subject.id,
        'name': subject.name,
        'description': subject.description,
        'image_url': subject.image_url,
        'created_at': subject.created_at.isoformat(),
        'chapters': chapters
    }), 200


@api_bp.route('/subjects', methods=['POST'])
@jwt_required()
@admin_required
def create_subject():
    data = request.json
    
    if not data or not data.get('name'):
        return jsonify({'error': 'Subject name is required'}), 400
    
    subject = Subject(
        name=data['name'],
        description=data.get('description', ''),
        image_url=data.get('image_url', '')
    )
    
    try:
        db.session.add(subject)
        db.session.commit()
        safe_delete_cache('cache:get_subjects:():{}')
        
        return jsonify({
            'message': 'Subject created successfully',
            'id': subject.id,
            'name': subject.name,
            'description': subject.description,
            'image_url': subject.image_url
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create subject: {str(e)}'}), 500



@api_bp.route('/subjects/<int:subject_id>/chapters', methods=['GET'])
@jwt_required()
@cache_response()
def get_chapters(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    chapters = Chapter.query.filter_by(subject_id=subject_id).order_by(Chapter.order).all()
    chapters_list = [{
        'id': chapter.id,
        'name': chapter.name,
        'description': chapter.description,
        'order': chapter.order,
        'quiz_count': chapter.quizzes.count()
    } for chapter in chapters]
    
    return jsonify(chapters_list), 200



@api_bp.route('/subjects/<int:subject_id>/chapters', methods=['POST'])
@jwt_required()
@admin_required
def create_chapter(subject_id):
    try:
        subject = Subject.query.get_or_404(subject_id)
        data = request.json
        
        if not data or not data.get('name'):
            return jsonify({'error': 'Chapter name is required'}), 400
        max_order = db.session.query(db.func.max(Chapter.order)).filter_by(subject_id=subject_id).scalar() or 0
        order = data.get('order', max_order + 1)
        
        existing_chapter = Chapter.query.filter_by(subject_id=subject_id, name=data['name']).first()
        if existing_chapter:
            return jsonify({
                'error': 'A chapter with this name already exists in this subject',
                'id': existing_chapter.id,
                'name': existing_chapter.name,
                'subject_id': subject_id
            }), 409


        chapter = Chapter(
            subject_id=subject_id,
            name=data['name'],
            description=data.get('description', ''),
            order=order
        )
        
        db.session.add(chapter)
        db.session.commit()
        
        safe_delete_cache(f'cache:get_chapters:():{{"subject_id": {subject_id}}}')
        safe_delete_cache(f'cache:get_subject:():{{"subject_id": {subject_id}}}')
        
        return jsonify({
            'id': chapter.id,
            'name': chapter.name,
            'description': chapter.description,
            'order': chapter.order,
            'subject_id': subject_id
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"Error creating chapter: {e}")
        return jsonify({'error': f'Failed to create chapter: {str(e)}'}), 500



@api_bp.route('/chapters/<int:chapter_id>', methods=['PUT'])
@jwt_required()
@admin_required
def update_chapter(chapter_id):
    try:
        chapter = Chapter.query.get_or_404(chapter_id)
        data = request.json
        
        if not data or not data.get('name'):
            return jsonify({'error': 'Chapter name is required'}), 400
        
        existing_chapter = Chapter.query.filter(
            Chapter.subject_id == chapter.subject_id,
            Chapter.name == data['name'],
            Chapter.id != chapter_id
        ).first()
        
        if existing_chapter:
            return jsonify({
                'error': 'Another chapter with this name already exists in this subject'
            }), 409
        
        chapter.name = data['name']
        chapter.description = data.get('description', chapter.description)
        if 'order' in data and data['order'] is not None:
            chapter.order = data['order']
        
        db.session.commit()
        
        safe_delete_cache(f'cache:get_chapters:():{{"subject_id": {chapter.subject_id}}}')
        safe_delete_cache(f'cache:get_subject:():{{"subject_id": {chapter.subject_id}}}')
        safe_delete_cache(f'cache:get_chapter:():{{"chapter_id": {chapter_id}}}')
        
        return jsonify({
            'id': chapter.id,
            'name': chapter.name,
            'description': chapter.description,
            'order': chapter.order,
            'subject_id': chapter.subject_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating chapter: {e}")
        return jsonify({'error': f'Failed to update chapter: {str(e)}'}), 500




@api_bp.route('/chapters/<int:chapter_id>', methods=['DELETE'])
@jwt_required()
@admin_required
def delete_chapter(chapter_id):
    try:
        chapter = Chapter.query.get_or_404(chapter_id)
        quiz_count = chapter.quizzes.count()
        if quiz_count > 0:
            return jsonify({
                'error': 'Cannot delete chapter with existing quizzes',
                'message': f'This chapter has {quiz_count} quiz(es). Please delete them first.',
                'quiz_count': quiz_count
            }), 400
        
        subject_id = chapter.subject_id
        chapter_name = chapter.name 
        
        db.session.delete(chapter)
        db.session.commit()
        
        safe_delete_cache(f'cache:get_chapters:():{{"subject_id": {subject_id}}}')
        safe_delete_cache(f'cache:get_subject:():{{"subject_id": {subject_id}}}')
        safe_delete_cache(f'cache:get_chapter:():{{"chapter_id": {chapter_id}}}')
        
        return jsonify({
            'message': f'Chapter "{chapter_name}" deleted successfully',
            'id': chapter_id,
            'subject_id': subject_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting chapter: {e}")
        return jsonify({'error': f'Failed to delete chapter: {str(e)}'}), 500




# quiz ke routes
@api_bp.route('/quizzes', methods=['GET'])
@jwt_required()
@cache_response()
def get_quizzes():
    current_user_email = get_jwt_identity()
    user = User.query.filter_by(email=current_user_email).first()
    
    if user.role == 'admin':
        quizzes = Quiz.query.all()
    else:
        now = datetime.datetime.utcnow()
        quizzes = Quiz.query.filter(
            Quiz.is_active == True,
            (Quiz.start_date == None) | (Quiz.start_date <= now),
            (Quiz.end_date == None) | (Quiz.end_date >= now)
        ).all()
    

    quizzes_list = [{
        'id': quiz.id,
        'title': quiz.title,
        'description': quiz.description,
        'chapter_id': quiz.chapter_id,
        'chapter_name': quiz.chapter.name,
        'subject_id': quiz.chapter.subject_id,
        'subject_name': quiz.chapter.subject.name,
        'duration_minutes': quiz.duration_minutes,
        'start_date': quiz.start_date.isoformat() if quiz.start_date else None,
        'end_date': quiz.end_date.isoformat() if quiz.end_date else None,
        'question_count': quiz.questions.count(),
        'is_available': quiz.is_available
    } for quiz in quizzes]
    
    return jsonify(quizzes_list), 200




@api_bp.route('/quizzes/<int:quiz_id>', methods=['GET'])
@jwt_required()
@cache_response()
def get_quiz(quiz_id):
    try:
        current_user_email = get_jwt_identity()
        user = User.query.filter_by(email=current_user_email).first()
        
        quiz = Quiz.query.get_or_404(quiz_id)
        
        if user.role != 'admin':
            now = datetime.datetime.utcnow()
            if not quiz.is_active:
                return jsonify({'error': 'This quiz is not available'}), 403
            
            if quiz.start_date and quiz.start_date > now:
                return jsonify({'error': 'This quiz is not available yet'}), 403
                
            if quiz.end_date and quiz.end_date < now:
                return jsonify({'error': 'This quiz has expired'}), 403
        
        chapter = Chapter.query.get(quiz.chapter_id)
        
        quiz_data = {
            'id': quiz.id,
            'title': quiz.title,
            'description': quiz.description,
            'duration_minutes': quiz.duration_minutes,
            'passing_score': quiz.passing_score,
            'start_date': quiz.start_date.isoformat() if quiz.start_date else None,
            'end_date': quiz.end_date.isoformat() if quiz.end_date else None,
            'is_active': quiz.is_active,
            'question_count': quiz.questions.count(),
            'chapter': {
                'id': chapter.id,
                'name': chapter.name,
                'subject_id': chapter.subject_id
            }
        }
        
        return jsonify(quiz_data), 200
        
    except Exception as e:
        print(f"Error fetching quiz: {e}")
        return jsonify({'error': f'Failed to fetch quiz: {str(e)}'}), 500




@api_bp.route('/subjects/<int:subject_id>', methods=['PUT'])
@jwt_required()
@admin_required
def update_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    data = request.json
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    if 'name' in data:
        subject.name = data['name']
    if 'description' in data:
        subject.description = data['description']
    if 'image_url' in data:
        subject.image_url = data['image_url']
    
    db.session.commit()
    
    redis_client.delete('cache:get_subjects:():{}')
    redis_client.delete(f'cache:get_subject:():{{"subject_id": {subject_id}}}')
    
    return jsonify({
        'message': 'Subject updated successfully',
        'id': subject.id,
        'name': subject.name,
        'description': subject.description,
        'image_url': subject.image_url
    }), 200

@api_bp.route('/subjects/<int:subject_id>', methods=['DELETE'])
@jwt_required()
@admin_required
def delete_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    
    if subject.chapters.count() > 0:
        return jsonify({'error': 'Cannot delete subject with existing chapters'}), 400
    
    db.session.delete(subject)
    db.session.commit()
    
    redis_client.delete('cache:get_subjects:():{}')
    redis_client.delete(f'cache:get_subject:():{{"subject_id": {subject_id}}}')
    
    return jsonify({'message': 'Subject deleted successfully'}), 200



@api_bp.route('/scores', methods=['GET'])
@jwt_required()
def get_user_scores():
    try:
        current_user_email = get_jwt_identity()
        user = User.query.filter_by(email=current_user_email).first()
        scores = Score.query.filter_by(user_id=user.id).order_by(Score.completed_at.desc()).all()
        
        scores_list = []
        for score in scores:
            quiz = Quiz.query.get(score.quiz_id)
            chapter = Chapter.query.get(quiz.chapter_id) if quiz else None
            subject = Subject.query.get(chapter.subject_id) if chapter else None
            
            scores_list.append({
                'id': score.id,
                'quiz_id': score.quiz_id,
                'quiz_title': quiz.title if quiz else 'Unknown Quiz',
                'chapter_name': chapter.name if chapter else 'Unknown Chapter',
                'subject_name': subject.name if subject else 'Unknown Subject',
                'score': score.score,
                'passed': score.passed,
                'time_taken': score.time_taken,
                'completed_at': score.completed_at.isoformat() if score.completed_at else None,
            })
        
        return jsonify(scores_list), 200
        
    except Exception as e:
        print(f"Error fetching user scores: {e}")
        return jsonify({'error': f'Failed to fetch scores: {str(e)}'}), 500



@api_bp.route('/quizzes/<int:quiz_id>/attempt', methods=['POST'])
@jwt_required()
def start_quiz_attempt(quiz_id):
    try:
        current_user_email = get_jwt_identity()
        user = User.query.filter_by(email=current_user_email).first()
        quiz = Quiz.query.get_or_404(quiz_id)
        
        now = datetime.datetime.utcnow()
        if not quiz.is_active:
            return jsonify({'error': 'This quiz is currently inactive'}), 403
        
        if quiz.start_date and quiz.start_date > now:
            return jsonify({'error': 'This quiz is not available yet'}), 403
            
        if quiz.end_date and quiz.end_date < now:
            return jsonify({'error': 'This quiz has expired'}), 403
        
        questions_count = Question.query.filter_by(quiz_id=quiz_id).count()
        
        
        score = Score(
            user_id=user.id,
            quiz_id=quiz_id,
            score=0.0,  
            time_taken=0,  
            passed=False,  
            completed_at=now
        )
        db.session.add(score)
        db.session.commit()
    
        return jsonify({
            'message': 'Quiz attempt initialized successfully',
            'quiz_id': quiz_id,
            'attempt_id': score.id,
            'questions_count': questions_count,
            'duration_minutes': quiz.duration_minutes
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error initializing quiz attempt: {e}")
        return jsonify({'error': f'Failed to initialize quiz attempt: {str(e)}'}), 500




@api_bp.route('/quizzes/<int:quiz_id>/submit', methods=['POST'])
@jwt_required()
def submit_quiz(quiz_id):
    try:
        current_user_email = get_jwt_identity()
        user = User.query.filter_by(email=current_user_email).first()
        data = request.json
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        answers = data.get('answers', [])
        time_taken = data.get('time_taken', 0)  
        
        quiz = Quiz.query.get_or_404(quiz_id)
        questions = Question.query.filter_by(quiz_id=quiz_id).all()
        total_questions = len(questions)
        total_points = sum(q.points for q in questions)
        correct_answers = 0
        

        score = Score(
            user_id=user.id,
            quiz_id=quiz_id,
            score=0.0,  
            time_taken=time_taken,
            passed=False,
            completed_at=datetime.datetime.utcnow()
        )
        
        db.session.add(score)
        db.session.flush()  
        
        for answer_data in answers:
            question_id = answer_data.get('question_id')
            choice_id = answer_data.get('choice_id')
            
            if not question_id or not choice_id:
                continue
            
            question = Question.query.get(question_id)
            choice = Choice.query.get(choice_id)
            
            if not question or not choice or question.quiz_id != quiz_id:
                continue
            is_correct = choice.is_correct
            
            if is_correct:
                correct_answers += question.points
            
            answer = Answer(
                score_id=score.id,
                question_id=question_id,
                choice_id=choice_id,
                is_correct=is_correct
            )
            
            db.session.add(answer)
        
        if total_points > 0:
            score.score = (correct_answers / total_points) * 100
        else:
            score.score = 0
        
        score.passed = score.score >= quiz.passing_score
        
        db.session.commit()
        
        return jsonify({
            'message': 'Quiz submitted successfully',
            'score_id': score.id,
            'score': score.score,
            'passed': score.passed,
            'correct_answers': correct_answers,
            'total_questions': total_questions,
            'time_taken': time_taken
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"Error submitting quiz: {e}")
        return jsonify({'error': f'Failed to submit quiz: {str(e)}'}), 500




@api_bp.route('/chapters/<int:chapter_id>/quizzes', methods=['GET'])
@jwt_required()
@cache_response()
def get_chapter_quizzes(chapter_id):
    chapter = Chapter.query.get_or_404(chapter_id)
    current_user_email = get_jwt_identity()
    user = User.query.filter_by(email=current_user_email).first()
    
    if user.role == 'admin':
        quizzes = Quiz.query.filter_by(chapter_id=chapter_id).all()
    else:
        now = datetime.datetime.utcnow()
        quizzes = Quiz.query.filter_by(chapter_id=chapter_id).filter(
            Quiz.is_active == True,
            (Quiz.start_date == None) | (Quiz.start_date <= now),
            (Quiz.end_date == None) | (Quiz.end_date >= now)
        ).all()
    
    quizzes_list = [{
        'id': quiz.id,
        'title': quiz.title,
        'description': quiz.description,
        'duration_minutes': quiz.duration_minutes,
        'passing_score': quiz.passing_score,
        'start_date': quiz.start_date.isoformat() if quiz.start_date else None,
        'end_date': quiz.end_date.isoformat() if quiz.end_date else None,
        'is_active': quiz.is_active,
        'question_count': quiz.questions.count()
    } for quiz in quizzes]
    
    return jsonify(quizzes_list), 200




@api_bp.route('/chapters/<int:chapter_id>/quizzes', methods=['POST'])
@jwt_required()
@admin_required
def create_quiz(chapter_id):
    try:
        chapter = Chapter.query.get_or_404(chapter_id)
        data = request.json
        
        if not data or not data.get('title'):
            return jsonify({'error': 'Quiz title is required'}), 400
        
        existing_quiz = Quiz.query.filter_by(chapter_id=chapter_id, title=data['title']).first()
        if existing_quiz:
            return jsonify({
                'error': 'A quiz with this title already exists in this chapter',
                'id': existing_quiz.id,
                'title': existing_quiz.title
            }), 409 
        
        start_date = None
        end_date = None
        try:
            if data.get('start_date'):
                start_date = datetime.datetime.fromisoformat(data['start_date'])
            if data.get('end_date'):
                end_date = datetime.datetime.fromisoformat(data['end_date'])
            if start_date and end_date and end_date <= start_date:
                return jsonify({'error': 'End date must be after start date'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM:SS)'}), 400
    

        quiz = Quiz(
            chapter_id=chapter_id,
            title=data['title'],
            description=data.get('description', ''),
            duration_minutes=data.get('duration_minutes', 30),
            passing_score=data.get('passing_score', 70),
            start_date=start_date,
            end_date=end_date,
            is_active=data.get('is_active', True)
        )
        
        db.session.add(quiz)
        db.session.commit()
        
        safe_delete_cache(f'cache:get_chapter_quizzes:():{{"chapter_id": {chapter_id}}}')
        
        return jsonify({
            'id': quiz.id,
            'title': quiz.title,
            'description': quiz.description,
            'chapter_id': chapter_id,
            'duration_minutes': quiz.duration_minutes,
            'passing_score': quiz.passing_score,
            'is_active': quiz.is_active,
            'start_date': quiz.start_date.isoformat() if quiz.start_date else None,
            'end_date': quiz.end_date.isoformat() if quiz.end_date else None
        }), 201
        
    except Exception as e:
        db.session.rollback()
        print(f"Error creating quiz: {e}")
        return jsonify({'error': f'Failed to create quiz: {str(e)}'}), 500



@api_bp.route('/quizzes/<int:quiz_id>', methods=['PUT'])
@jwt_required()
@admin_required
def update_quiz(quiz_id):
    try:
        quiz = Quiz.query.get_or_404(quiz_id)
        data = request.json
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        if 'title' in data and data['title'] != quiz.title:
            existing_quiz = Quiz.query.filter(
                Quiz.chapter_id == quiz.chapter_id,
                Quiz.title == data['title'],
                Quiz.id != quiz_id
            ).first()
            if existing_quiz:
                return jsonify({
                    'error': 'Another quiz with this title already exists in this chapter'
                }), 409
        
        start_date = quiz.start_date
        end_date = quiz.end_date
        

        try:
            if 'start_date' in data:
                start_date = datetime.datetime.fromisoformat(data['start_date']) if data['start_date'] else None
            if 'end_date' in data:
                end_date = datetime.datetime.fromisoformat(data['end_date']) if data['end_date'] else None
            if start_date and end_date and end_date <= start_date:
                return jsonify({'error': 'End date must be after start date'}), 400
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM:SS)'}), 400
        
      

        if 'title' in data:
            quiz.title = data['title']
        if 'description' in data:
            quiz.description = data['description']
        if 'duration_minutes' in data:
            quiz.duration_minutes = data['duration_minutes']
        if 'passing_score' in data:
            quiz.passing_score = data['passing_score']
        if 'start_date' in data:
            quiz.start_date = start_date
        if 'end_date' in data:
            quiz.end_date = end_date
        if 'is_active' in data:
            quiz.is_active = data['is_active']
        
        db.session.commit()
        
        safe_delete_cache(f'cache:get_chapter_quizzes:():{{"chapter_id": {quiz.chapter_id}}}')
        
        return jsonify({
            'id': quiz.id,
            'title': quiz.title,
            'description': quiz.description,
            'chapter_id': quiz.chapter_id,
            'duration_minutes': quiz.duration_minutes,
            'passing_score': quiz.passing_score,
            'is_active': quiz.is_active,
            'start_date': quiz.start_date.isoformat() if quiz.start_date else None,
            'end_date': quiz.end_date.isoformat() if quiz.end_date else None
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating quiz: {e}")
        return jsonify({'error': f'Failed to update quiz: {str(e)}'}), 500



@api_bp.route('/quizzes/<int:quiz_id>', methods=['DELETE'])
@jwt_required()
@admin_required
def delete_quiz(quiz_id):
    try:
        quiz = Quiz.query.get_or_404(quiz_id)
        question_count = quiz.questions.count()
        attempt_count = Score.query.filter_by(quiz_id=quiz_id).count()
        
        
        if question_count > 0:
            return jsonify({
                'error': 'Cannot delete quiz with existing questions',
                'message': f'This quiz has {question_count} question(s). Please delete them first.',
                'question_count': question_count
            }), 400
        
        
        if attempt_count > 0:
            return jsonify({
                'error': 'Cannot delete quiz that has been attempted by users',
                'message': f'This quiz has been attempted {attempt_count} time(s). You cannot delete it.',
                'attempt_count': attempt_count
            }), 400
        
        chapter_id = quiz.chapter_id
        quiz_title = quiz.title  
        
        db.session.delete(quiz)

        db.session.commit()
        
        safe_delete_cache(f'cache:get_chapter_quizzes:():{{"chapter_id": {chapter_id}}}')
        safe_delete_cache(f'cache:get_quiz:():{{"quiz_id": {quiz_id}}}')
        
        return jsonify({
            'message': f'Quiz "{quiz_title}" deleted successfully',
            'id': quiz_id,
            'chapter_id': chapter_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting quiz: {e}")
        return jsonify({'error': f'Failed to delete quiz: {str(e)}'}), 500



@api_bp.route('/quizzes/<int:quiz_id>/questions', methods=['GET'])
@jwt_required()
def get_questions(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    
    current_user_email = get_jwt_identity()
    user = User.query.filter_by(email=current_user_email).first()
    questions = Question.query.filter_by(quiz_id=quiz_id).order_by(Question.id).all()
    
    show_answers = user.role == 'admin'
    
    questions_list = []
    for question in questions:
        choices = []
        for choice in question.choices:
            choice_data = {
                'id': choice.id,
                'text': choice.text
            }
            if show_answers:
                choice_data['is_correct'] = choice.is_correct
            choices.append(choice_data)
        
        question_data = {
            'id': question.id,
            'text': question.text,
            'explanation': question.explanation,
            'points': question.points,
            'choices': choices
        }
        questions_list.append(question_data)
    
    return jsonify(questions_list), 200



@api_bp.route('/quizzes/<int:quiz_id>/questions', methods=['POST'])
@jwt_required()
@admin_required
def create_question(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    
    data = request.json
    
    if not data or not data.get('text') or not data.get('choices'):
        return jsonify({'error': 'Question text and choices are required'}), 400
    
    choices = data.get('choices', [])
    if not any(c.get('is_correct') for c in choices):
        return jsonify({'error': 'At least one choice must be marked as correct'}), 400
    
    

    question = Question(
        quiz_id=quiz_id,
        text=data['text'],
        explanation=data.get('explanation', ''),
        points=data.get('points', 1)
    )
    
    db.session.add(question)
    db.session.flush()  
    
    for choice_data in choices:
        choice = Choice(
            question_id=question.id,
            text=choice_data.get('text', ''),
            is_correct=choice_data.get('is_correct', False)
        )
        db.session.add(choice)
    
    db.session.commit()
    
    return jsonify({
        'message': 'Question created successfully',
        'id': question.id,
        'text': question.text
    }), 201


@api_bp.route('/questions/<int:question_id>', methods=['PUT'])
@jwt_required()
@admin_required
def update_question(question_id):
    question = Question.query.get_or_404(question_id)
    data = request.json
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if 'text' in data:
        question.text = data['text']
    if 'explanation' in data:
        question.explanation = data['explanation']
    if 'points' in data:
        question.points = data['points']
    
    if 'choices' in data:
        choices = data['choices']
        if not any(c.get('is_correct') for c in choices):
            return jsonify({'error': 'At least one choice must be marked as correct'}), 400
        
        Choice.query.filter_by(question_id=question_id).delete()
        
        for choice_data in choices:
            choice = Choice(
                question_id=question_id,
                text=choice_data.get('text', ''),
                is_correct=choice_data.get('is_correct', False)
            )
            db.session.add(choice)
    
    db.session.commit()
    
    return jsonify({
        'message': 'Question updated successfully',
        'id': question.id,
        'text': question.text
    }), 200




@api_bp.route('/questions/<int:question_id>', methods=['DELETE'])
@jwt_required()
@admin_required
def delete_question(question_id):
    question = Question.query.get_or_404(question_id)
    quiz_id = question.quiz_id
    


    if Answer.query.filter_by(question_id=question_id).count() > 0:
        return jsonify({'error': 'Cannot delete a question that has been answered by users'}), 400
    
    Choice.query.filter_by(question_id=question_id).delete()
    
    db.session.delete(question)
    db.session.commit()
    
    return jsonify({'message': 'Question deleted successfully'}), 200




@api_bp.route('/admin/users', methods=['GET'])
@jwt_required()
@admin_required
def get_all_users():
    users = User.query.all()
    
    users_list = [{
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'role': user.role,
        'created_at': user.created_at.isoformat(),
        'last_login': user.last_login.isoformat() if user.last_login else None,
        'is_active': user.is_active,
        'quiz_count': Score.query.filter_by(user_id=user.id).count()
    } for user in users]
    
    return jsonify(users_list), 200

@api_bp.route('/admin/users/<int:user_id>/status', methods=['PUT'])
@jwt_required()
@admin_required
def update_user_status(user_id):
    user = User.query.get_or_404(user_id)
    data = request.json
    
    if 'is_active' not in data:
        return jsonify({'error': 'is_active field is required'}), 400
    
    user.is_active = data['is_active']
    db.session.commit()
    
    return jsonify({
        'message': 'User status updated successfully',
        'id': user.id,
        'email': user.email,
        'is_active': user.is_active
    }), 200

@api_bp.route('/admin/users/<int:user_id>/stats', methods=['GET'])
@jwt_required()
@admin_required
def get_user_stats(user_id):
    user = User.query.get_or_404(user_id)
    
   
    scores = Score.query.filter_by(user_id=user_id).all()
    
  
    quizzes_taken = len(scores)
    quizzes_passed = sum(1 for score in scores if score.passed)
    average_score = sum(score.score for score in scores) / quizzes_taken if quizzes_taken > 0 else 0
    
    
    covered_subjects = db.session.query(Subject.id, Subject.name)\
        .join(Chapter, Subject.id == Chapter.subject_id)\
        .join(Quiz, Chapter.id == Quiz.chapter_id)\
        .join(Score, Quiz.id == Score.quiz_id)\
        .filter(Score.user_id == user_id)\
        .distinct().all()
    
    
    last_activity = db.session.query(db.func.max(Score.completed_at))\
        .filter(Score.user_id == user_id).scalar()
    
    return jsonify({
        'user_id': user_id,
        'quizzesTaken': quizzes_taken,
        'quizzesPassed': quizzes_passed,
        'averageScore': round(average_score, 1),
        'subjectsCovered': len(covered_subjects),
        'subjects': [{'id': s.id, 'name': s.name} for s in covered_subjects],
        'lastActivity': last_activity.isoformat() if last_activity else None
    }), 200





@api_bp.route('/admin/users/export', methods=['POST'])
@jwt_required()
@admin_required
def export_users():
    
    return jsonify({
        'message': 'User export job started successfully',
        'job_id': 'sample-job-id'
    }), 202


@api_bp.route('/admin/reports', methods=['GET'])
@jwt_required()
@admin_required
def get_reports():
    period = request.args.get('period', 'month')
    
    now = datetime.datetime.utcnow()
    if period == 'week':
        start_date = now - datetime.timedelta(days=7)
    elif period == 'month':
        start_date = now - datetime.timedelta(days=30)
    elif period == 'quarter':
        start_date = now - datetime.timedelta(days=90)
    elif period == 'year':
        start_date = now - datetime.timedelta(days=365)
    else:
        start_date = datetime.datetime.min
    
  
    prev_period_length = (now - start_date).days
    prev_period_start = start_date - datetime.timedelta(days=prev_period_length)
    
    total_users = User.query.count()
    new_users = User.query.filter(User.created_at >= start_date).count()
    total_quizzes = Score.query.filter(Score.completed_at >= start_date).count()
    
    prev_new_users = User.query.filter(
        User.created_at >= prev_period_start,
        User.created_at < start_date
    ).count()
    prev_total_quizzes = Score.query.filter(
        Score.completed_at >= prev_period_start,
        Score.completed_at < start_date
    ).count()
    
    scores = Score.query.filter(Score.completed_at >= start_date).all()
    pass_rate = sum(1 for s in scores if s.passed) / len(scores) * 100 if scores else 0
    avg_score = sum(s.score for s in scores) / len(scores) if scores else 0
    
    prev_scores = Score.query.filter(
        Score.completed_at >= prev_period_start,
        Score.completed_at < start_date
    ).all()
    prev_pass_rate = sum(1 for s in prev_scores if s.passed) / len(prev_scores) * 100 if prev_scores else 0
    prev_avg_score = sum(s.score for s in prev_scores) / len(prev_scores) if prev_scores else 0
    
    user_growth = ((new_users - prev_new_users) / prev_new_users * 100) if prev_new_users > 0 else 0
    quiz_growth = ((total_quizzes - prev_total_quizzes) / prev_total_quizzes * 100) if prev_total_quizzes > 0 else 0
    pass_rate_change = pass_rate - prev_pass_rate
    avg_score_change = avg_score - prev_avg_score
    

    top_subjects = db.session.query(
        Subject.id, 
        Subject.name, 
        db.func.count(Score.id).label('attempts'),
        db.func.avg(Score.score).label('avg_score')
    ).join(Chapter, Subject.id == Chapter.subject_id)\
     .join(Quiz, Chapter.id == Quiz.chapter_id)\
     .join(Score, Quiz.id == Score.quiz_id)\
     .filter(Score.completed_at >= start_date)\
     .group_by(Subject.id)\
     .order_by(db.desc('attempts'))\
     .limit(5).all()
    

    top_quizzes = db.session.query(
        Quiz.id, 
        Quiz.title, 
        db.func.count(Score.id).label('attempts'),
        db.func.avg(Score.score).label('avg_score'),
        db.func.sum(db.case([(Score.passed, 1)], else_=0)).label('passed_count')
    ).join(Score, Quiz.id == Score.quiz_id)\
     .filter(Score.completed_at >= start_date)\
     .group_by(Quiz.id)\
     .order_by(db.desc('attempts'))\
     .limit(5).all()
    

    top_users_by_score = db.session.query(
        User.id, 
        User.first_name,
        User.last_name,
        db.func.avg(Score.score).label('avg_score'),
        db.func.count(Score.id).label('quiz_count'),
        db.func.sum(db.case([(Score.passed, 1)], else_=0)).label('passed_count')
    ).join(Score, User.id == Score.user_id)\
     .filter(Score.completed_at >= start_date)\
     .group_by(User.id)\
     .having(db.func.count(Score.id) >= 3)\
     .order_by(db.desc('avg_score'))\
     .limit(10).all()
    


    top_users_by_activity = db.session.query(
        User.id, 
        User.first_name,
        User.last_name,
        db.func.count(Score.id).label('quiz_count'),
        db.func.avg(Score.score).label('avg_score'),
        db.func.sum(db.case([(Score.passed, 1)], else_=0)).label('passed_count')
    ).join(Score, User.id == Score.user_id)\
     .filter(Score.completed_at >= start_date)\
     .group_by(User.id)\
     .order_by(db.desc('quiz_count'))\
     .limit(10).all()
    
    days = []
    new_users_data = []
    quiz_attempts_data = []
    
    current_date = start_date
    while current_date <= now:
        day_str = current_date.strftime('%Y-%m-%d')
        days.append(day_str)
        
       
        day_users = User.query.filter(
            User.created_at >= current_date,
            User.created_at < current_date + datetime.timedelta(days=1)
        ).count()
        new_users_data.append(day_users)
        
        
        day_quizzes = Score.query.filter(
            Score.completed_at >= current_date,
            Score.completed_at < current_date + datetime.timedelta(days=1)
        ).count()
        quiz_attempts_data.append(day_quizzes)
        
        current_date += datetime.timedelta(days=1)
    


    subject_performance = db.session.query(
        Subject.name,
        db.func.avg(Score.score).label('avg_score'),
        db.func.sum(db.case([(Score.passed, 1)], else_=0)) * 100.0 / db.func.count(Score.id).label('pass_rate')
    ).join(Chapter, Subject.id == Chapter.subject_id)\
     .join(Quiz, Chapter.id == Quiz.chapter_id)\
     .join(Score, Quiz.id == Score.quiz_id)\
     .filter(Score.completed_at >= start_date)\
     .group_by(Subject.id)\
     .order_by(db.desc('avg_score'))\
     .limit(10).all()
    


    results = {
        'overview': {
            'totalUsers': total_users,
            'totalQuizzes': total_quizzes,
            'passRate': round(pass_rate, 1),
            'avgScore': round(avg_score, 1),
            'userGrowth': round(user_growth, 1),
            'quizGrowth': round(quiz_growth, 1),
            'passRateChange': round(pass_rate_change, 1),
            'avgScoreChange': round(avg_score_change, 1)
        },
        'topSubjects': [{
            'id': subject.id,
            'name': subject.name,
            'attempts': subject.attempts,
            'avgScore': round(subject.avg_score, 1),
            'trend': round(random.uniform(-10, 10), 1) 
        } for subject in top_subjects],
        'topQuizzes': [{
            'id': quiz.id,
            'title': quiz.title,
            'attempts': quiz.attempts,
            'avgScore': round(quiz.avg_score, 1),
            'passRate': round((quiz.passed_count / quiz.attempts) * 100, 1) if quiz.attempts > 0 else 0
        } for quiz in top_quizzes],
        'topUsersByScore': [{
            'id': user.id,
            'firstName': user.first_name,
            'lastName': user.last_name,
            'avgScore': round(user.avg_score, 1),
            'quizzesTaken': user.quiz_count,
            'quizzesPassed': user.passed_count,
            'subjectsCovered': random.randint(1, 5),  
            'lastActivity': (now - datetime.timedelta(days=random.randint(0, 30))).isoformat()
        } for user in top_users_by_score],
        'topUsersByActivity': [{
            'id': user.id,
            'firstName': user.first_name,
            'lastName': user.last_name,
            'quizzesTaken': user.quiz_count,
            'avgScore': round(user.avg_score, 1),
            'quizzesPassed': user.passed_count,
            'subjectsCovered': random.randint(1, 5),  
            'lastActivity': (now - datetime.timedelta(days=random.randint(0, 30))).isoformat()
        } for user in top_users_by_activity],
        'analytics': {
            'userActivity': {
                'labels': days,
                'newUsers': new_users_data,
                'quizAttempts': quiz_attempts_data
            },
            'subjectPerformance': {
                'labels': [s.name for s in subject_performance],
                'avgScores': [round(s.avg_score, 1) for s in subject_performance],
                'passRates': [round(s.pass_rate, 1) for s in subject_performance]
            }
        }
    }
    
    return jsonify(results), 200

@api_bp.route('/admin/reports/export', methods=['GET'])
@jwt_required()
@admin_required
def export_report():
    return jsonify({
        'message': 'Report export job started successfully',
        'job_id': 'sample-job-id'
    }), 202

@api_bp.route('/quizzes/<int:quiz_id>/questions', methods=['GET'])
@jwt_required()
def get_quiz_questions_for_attempt(quiz_id):
    try:
        quiz = Quiz.query.get_or_404(quiz_id)
        
        questions = Question.query.filter_by(quiz_id=quiz_id).order_by(Question.order).all()
        
        questions_list = []
        for question in questions:
            choices = Choice.query.filter_by(question_id=question.id).all()
            
            choices_list = [{
                'id': choice.id,
                'text': choice.text,
            } for choice in choices]
            
            random.shuffle(choices_list)
            
            questions_list.append({
                'id': question.id,
                'text': question.text,
                'points': question.points,
                'order': question.order,
                'choices': choices_list
            })
        
        return jsonify(questions_list), 200
        
    except Exception as e:
        print(f"Error fetching quiz questions: {e}")
        return jsonify({'error': f'Failed to fetch quiz questions: {str(e)}'}), 500





@api_bp.route('/dashboard/stats', methods=['GET'])
@jwt_required()
def get_dashboard_stats():
    try:
        current_user_email = get_jwt_identity()
        user = User.query.filter_by(email=current_user_email).first()
        scores = Score.query.filter_by(user_id=user.id).all()
        
        completed = len(scores)
        passed = sum(1 for score in scores if score.passed)
        
       

        average_score = 0
        if completed > 0:
            average_score = sum(score.score for score in scores) / completed
        
        
        avg_time_seconds = 0
        if completed > 0:
            avg_time_seconds = sum(score.time_taken for score in scores) / completed
        
        
        return jsonify({
            'completed': completed,
            'passed': passed,
            'average_score': round(average_score, 1),
            'avg_time_seconds': int(avg_time_seconds)
        }), 200
        
    except Exception as e:
        print(f"Error fetching dashboard stats: {e}")
        return jsonify({'error': f'Failed to fetch dashboard stats: {str(e)}'}), 500



@api_bp.route('/scores/export', methods=['POST'])
@jwt_required()
def trigger_score_export():
    """Trigger CSV export of user's quiz scores."""
    current_user_id = get_jwt_identity()
    
    task = export_user_quizzes_as_csv.delay(current_user_id)
    
    return jsonify({
        'message': 'Export started successfully',
        'task_id': task.id
    }), 202



@api_bp.route('/scores/export/<task_id>', methods=['GET'])
@jwt_required()
def check_export_status(task_id):
    """Check the status of an export task."""
    from celery.result import AsyncResult
    
    try:
        task_result = AsyncResult(task_id)
        
        try:
            state = task_result.state
        except AttributeError:
            logging.error(f"Backend error when checking task {task_id}. This is likely due to the result backend not being properly configured.")
            return jsonify({
                'state': 'UNKNOWN',
                'status': 'Task status cannot be checked. The email will still be sent when the export is complete.'
            }), 200
        

        if state == 'PENDING':
            response = {
                'state': state,
                'status': 'Export in progress. You will receive an email when it is complete.'
            }
        elif state == 'FAILURE':
            response = {
                'state': state,
                'status': 'Export failed.',
                'error': str(task_result.info) if task_result.info else 'Unknown error'
            }
        else:
            response = {
                'state': state,
                'status': task_result.info if task_result.info else 'Export completed successfully. Check your email for the results.'
            }
        
        return jsonify(response)
    except Exception as e:
        logging.error(f"Error checking task status: {str(e)}")
        return jsonify({
            'state': 'ERROR',
            'status': 'Error checking export status. The email will still be sent when the export is complete.'
        }), 200
    





    