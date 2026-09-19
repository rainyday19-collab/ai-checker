import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import { BrowserRouter } from 'react-router-dom';
import './styles.css';
import { ToastProvider } from './UI.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter><ToastProvider><App /></ToastProvider></BrowserRouter>
  </React.StrictMode>,
);
