import os
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from flask_mysqldb import MySQL
import paho.mqtt.client as mqtt
import json

# Configuração de logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.config['SESSION_PERMANENT'] = False  # Sessão não persiste após fechar o navegador
app.secret_key = 'your_secret_key'
bcrypt = Bcrypt(app)

# Configurações do banco de dados MySQL
app.config['MYSQL_HOST'] = 'sql10.freemysqlhosting.net'
app.config['MYSQL_USER'] = 'sql10746168'
app.config['MYSQL_PASSWORD'] = 'Py2RUvw6my'
app.config['MYSQL_DB'] = 'sql10746168'

mysql = MySQL(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Tópicos MQTT
TOPIC_STATUS = "home/esp32/status"
TOPIC_COMMAND = "home/esp32/leds"

# Estado inicial dos LEDs
led_status = {"led1": False, "led2": False, "led3": False, "led4": False}

# Classe do usuário
class User(UserMixin):
    def __init__(self, id, email, broker, username, password, port):
        self.id = id
        self.email = email
        self.broker = broker
        self.username = username
        self.password = password
        self.port = port

@login_manager.user_loader
def load_user(user_id):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    if user:
        logging.info(f"Usuário carregado: {user[1]}")
        return User(user[0], user[1], user[3], user[4], user[5], user[6])
    logging.warning(f"Usuário com ID {user_id} não encontrado.")
    return None

# Configuração MQTT
mqtt_client = mqtt.Client()
mqtt_client._initialized = False

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Conectado ao broker MQTT com sucesso!")
        client.subscribe(TOPIC_STATUS)
        logging.debug(f"Inscrito no tópico: {TOPIC_STATUS}")
    else:
        logging.error(f"Falha ao conectar ao broker MQTT: Código {rc}")

def on_message(client, userdata, msg):
    global led_status
    try:
        payload = json.loads(msg.payload.decode())
        for led, state in payload.items():
            if led in led_status:
                led_status[led] = state
        logging.info(f"Estado dos LEDs atualizado: {led_status}")
    except Exception as e:
        logging.error(f"Erro ao processar mensagem MQTT: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        broker = request.form['broker']
        mqtt_user = request.form['mqtt_user']
        mqtt_password = request.form['mqtt_password']
        mqtt_port = int(request.form['mqtt_port'])

        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        cur = mysql.connection.cursor()
        try:
            cur.execute("""
                INSERT INTO users (email, password_hash, mqtt_broker, mqtt_username, mqtt_password, mqtt_port)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (email, hashed_password, broker, mqtt_user, mqtt_password, mqtt_port))
            mysql.connection.commit()
            logging.info(f"Novo usuário registrado: {email}")
            flash("Registrado com sucesso!", "success")
            return redirect(url_for('login'))
        except Exception as e:
            logging.error(f"Erro ao registrar usuário {email}: {e}")
            flash(f"Erro ao registrar: {e}", "danger")
        finally:
            cur.close()
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        cur = mysql.connection.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()

        if user and bcrypt.check_password_hash(user[2], password):
            user_obj = User(user[0], user[1], user[3], user[4], user[5], user[6])
            login_user(user_obj)
            # Log das informações do usuário
            logging.info(f"Usuário autenticado: {email}")
            logging.debug(f"Broker: {user[3]}, Username: {user[4]}, Password: {user[5]}, Port: {user[6]}")
            flash("login realizado com sucesso!")
            return redirect(url_for('dashboard'))
        else:
            logging.warning(f"Tentativa de login falhou para o email: {email}")
            flash("Credenciais inválidas!", "danger")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logging.info(f"Usuário desconectado: {current_user.email}")
    logout_user()
    flash("Você saiu da conta.", "info")
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    global mqtt_client
    if not mqtt_client._initialized:
        mqtt_client.tls_set()  # Configura conexão segura
        mqtt_client.tls_insecure_set(True)  # Ignora a validação de certificado
        mqtt_client.username_pw_set(current_user.username, current_user.password)
        mqtt_client.connect(current_user.broker, int(current_user.port))
        mqtt_client.username_pw_set(current_user.username, current_user.password)
        mqtt_client.loop_start()
        mqtt_client._initialized = True
        logging.info(f"Cliente MQTT inicializado para o usuário: {current_user.email}")
    return render_template('index.html', broker=current_user.broker)

@app.route('/update_led', methods=['POST'])
@login_required
def update_led():
    global led_status
    data = request.get_json()
    for led, state in data.items():
        if led in led_status:
            led_status[led] = bool(state)
            logging.info(f"{led} {'ligado' if state else 'desligado'} pelo usuário {current_user.email}")
    mqtt_payload = json.dumps(led_status)
    mqtt_client.publish(TOPIC_COMMAND, mqtt_payload)
    logging.debug(f"Mensagem publicada no tópico {TOPIC_COMMAND}: {mqtt_payload}")
    logging.debug(f"Mensagem enviada para o broker: {mqtt_payload}")
    return jsonify({"status": "OK", "led_status": led_status}), 200

@app.route('/led_status', methods=['GET'])
@login_required
def get_led_status():
    logging.info(f"Status dos LEDs solicitado por {current_user.email}: {led_status}")
    return jsonify(led_status)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logging.info("Iniciando o servidor Flask...")
    app.run(host='0.0.0.0', port=port)
