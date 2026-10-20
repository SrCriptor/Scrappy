FROM python:3.10-slim

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

# Adicionada a flag para ignorar o aviso de root e atualizar o pip silenciosamente
RUN pip install --root-user-action=ignore --no-cache-dir --upgrade pip

# Adicionada a flag para ignorar o aviso de root na instalação das dependências
RUN pip install --root-user-action=ignore --no-cache-dir --upgrade -r /code/requirements.txt

COPY . .

# Alterada a porta final para 8080, garantindo compatibilidade com o Back4app
CMD ["gunicorn", "--timeout", "300", "-k", "gevent", "-b", "0.0.0.0:8080", "app:app"]
