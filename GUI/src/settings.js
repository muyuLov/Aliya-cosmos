import { createApp } from 'vue';
import { createPinia } from 'pinia';
import AppSettings from './AppSettings.vue';
import './styles/tokens.css';
import './styles/base.css';
import './styles/settings.css';

const app = createApp(AppSettings);
app.use(createPinia());
app.mount('#app');
