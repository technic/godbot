import requests
from PIL import Image
from io import BytesIO

r = requests.post(
    'https://carbon.now.sh/api/image',
    json={'code': 'print("Hello World")'},
    headers={'Auth': 'Bearer 1234', 'origin': 'https://carbon.now.sh'})
print(r.text)
r.raise_for_status()

Image.open(BytesIO(r.content)).show()
