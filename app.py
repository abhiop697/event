from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from pymongo import MongoClient
from bson.objectid import ObjectId
from flask_socketio import SocketIO, send, join_room, leave_room
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
import os
from datetime import datetime

# Initialize Flask app
app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a secure secret key

# Configure MongoDB
client = MongoClient('mongodb://localhost:27017/')
db = client['event_platform']
users_collection = db['users']
events_collection = db['events']
attendance_collection = db['attendance']
chats_collection = db['chats']
private_chats_collection = db['private_chats']

# Configure Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Set the login view

# Configure Flask-SocketIO
socketio = SocketIO(app)

# Configure Flask-Mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your_email@gmail.com'  # Replace with your email
app.config['MAIL_PASSWORD'] = 'your_password'  # Replace with your email password
mail = Mail(app)

# Configure file uploads
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.role = user_data['role']

@login_manager.user_loader
def load_user(user_id):
    user_data = users_collection.find_one({'_id': ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None

# Helper function to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Routes
@app.route('/')
def home():
    events = list(events_collection.find())
    return render_template('home.html', events=events)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        if users_collection.find_one({'username': username}):
            flash('Username already exists!', 'error')
            return redirect(url_for('register'))

        users_collection.insert_one({
            'username': username,
            'password': password,  # In a real app, hash the password before saving
            'role': role,
            'bio': '',
            'interests': ''
        })

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/delete_event/<event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    events_collection.delete_one({'_id': ObjectId(event_id)})
    flash('Event deleted successfully!', 'success')
    return redirect(url_for('events'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user_data = users_collection.find_one({'username': username, 'password': password})

        if user_data:
            user = User(user_data)
            login_user(user)
            return redirect(url_for('events'))  # Redirecting to events page after login
        else:
            flash('Invalid username or password!', 'error')

    return render_template('login.html')


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        bio = request.form['bio']
        interests = request.form['interests']

        users_collection.update_one(
            {'_id': ObjectId(current_user.id)},
            {'$set': {'bio': bio, 'interests': interests}}
        )

        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))

    user_data = users_collection.find_one({'_id': ObjectId(current_user.id)})
    return render_template('profile.html', user=user_data)

@app.route('/create_event', methods=['GET', 'POST'])
@login_required
def create_event():
    if current_user.role != 'organizer':
        flash('Unauthorized: Only organizers can create events.', 'error')
        return redirect(url_for('home'))

    if request.method == 'POST':
        event = {
            'name': request.form['name'],
            'description': request.form['description'],
            'organizer': current_user.username,
            'picture': ''
        }

        # Handle file upload
        if 'picture' in request.files:
            file = request.files['picture']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                event['picture'] = filename

        events_collection.insert_one(event)
        flash('Event created successfully!', 'success')
        return redirect(url_for('home'))

    return render_template('create_event.html')

@app.route('/event/<event_id>', methods=['GET', 'POST'])
def event_details(event_id):
    event = events_collection.find_one({'_id': ObjectId(event_id)})
    attendees = list(attendance_collection.find({'event_id': event_id}))

    # Check if the current user has already registered for the event
    is_registered = False
    if current_user.is_authenticated:
        is_registered = attendance_collection.find_one({
            'event_id': event_id,
            'user_id': current_user.id
        }) is not None

    if request.method == 'POST' and current_user.is_authenticated:
        if is_registered:
            flash('You have already registered for this event!', 'error')
        else:
            # Register user for the event
            attendance_collection.insert_one({
                'event_id': event_id,
                'user_id': current_user.id,
                'username': current_user.username
            })
            flash('You have successfully registered for the event!', 'success')
            return redirect(url_for('event_details', event_id=event_id))

    return render_template('event_details.html', event=event, attendees=attendees, is_registered=is_registered)

@app.route('/chat/<event_id>')
@login_required
def chat(event_id):
    # Ensure the user has attended the event
    if not attendance_collection.find_one({'event_id': event_id, 'user_id': current_user.id}):
        flash('You must attend the event to access the chat!', 'error')
        return redirect(url_for('event_details', event_id=event_id))

    # Fetch event details
    event = events_collection.find_one({'_id': ObjectId(event_id)})
    attendees = list(attendance_collection.find({'event_id': event_id}))

    # Fetch group chat history
    chat_history = list(chats_collection.find({'event_id': event_id}))

    return render_template('chat.html', event=event, attendees=attendees, chat_history=chat_history)

@app.route('/private_chat/<recipient_id>')
@login_required
def private_chat(recipient_id):
    recipient = users_collection.find_one({'_id': ObjectId(recipient_id)})
    if not recipient:
        flash('Recipient not found!', 'error')
        return redirect(url_for('home'))

    # Fetch private chat history
    private_chat_history = list(private_chats_collection.find({
        '$or': [
            {'sender_id': current_user.id, 'recipient_id': recipient_id},
            {'sender_id': recipient_id, 'recipient_id': current_user.id}
        ]
    }))

    return render_template('private_chat.html', recipient=recipient, private_chat_history=private_chat_history)

@app.route('/search')
def search():
    query = request.args.get('query')
    events = list(events_collection.find({'$text': {'$search': query}}))
    users = list(users_collection.find({'$text': {'$search': query}}))
    return render_template('search.html', events=events, users=users)

@app.route('/events')
def events():
    page = int(request.args.get('page', 1))
    per_page = 10
    events = list(events_collection.find().skip((page - 1) * per_page).limit(per_page))
    return render_template('events.html', events=events, page=page)

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST' and 'photo' in request.files:
        file = request.files['photo']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            users_collection.update_one(
                {'_id': ObjectId(current_user.id)},
                {'$set': {'profile_picture': filename}}
            )
            flash('Profile picture uploaded successfully!', 'success')
            return redirect(url_for('profile'))
        else:
            flash('Invalid file type! Allowed types: png, jpg, jpeg, gif.', 'error')
    return render_template('upload.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

# SocketIO Events
@socketio.on('join')
def handle_join(data):
    event_id = data['event_id']
    join_room(event_id)
    send(f'{current_user.username} has joined the chat.', room=event_id)

@socketio.on('leave')
def handle_leave(data):
    event_id = data['event_id']
    leave_room(event_id)
    send(f'{current_user.username} has left the chat.', room=event_id)

@socketio.on('group_message')
def handle_group_message(data):
    event_id = data['event_id']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Save the message to the database
    chats_collection.insert_one({
        'event_id': event_id,
        'sender_id': current_user.id,
        'sender_username': current_user.username,
        'message': message,
        'timestamp': timestamp
    })

    # Broadcast the message to the room
    send({
        'sender_username': current_user.username,
        'message': message,
        'timestamp': timestamp
    }, room=event_id)

@socketio.on('private_message')
def handle_private_message(data):
    recipient_id = data['recipient_id']
    message = data['message']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Save the private message to the database
    private_chats_collection.insert_one({
        'sender_id': current_user.id,
        'sender_username': current_user.username,
        'recipient_id': recipient_id,
        'message': message,
        'timestamp': timestamp
    })

    # Send the message to the recipient
    send({
        'sender_username': current_user.username,
        'message': message,
        'timestamp': timestamp
    }, room=recipient_id)

# Helper Functions
def send_email(subject, recipient, body):
    msg = Message(subject, recipients=[recipient])
    msg.body = body
    mail.send(msg)

if __name__ == '__main__':
    socketio.run(app, debug=True)