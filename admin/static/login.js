document.querySelector('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('button');
  button.disabled = true;
  document.querySelector('#login-error').textContent = '';
  try {
    const values = Object.fromEntries(new FormData(event.target));
    const response = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(values)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '登录失败');
    location.replace('/');
  } catch (error) {
    document.querySelector('#login-error').textContent = error.message;
  } finally {button.disabled = false;}
});
