// Configuração do Tailwind (CDN) compartilhada pelas páginas. Carregar DEPOIS do script do CDN.
tailwind.config = {
    darkMode: 'class',
    theme: { extend: {
        colors: { brand: { 50: '#f0f9ff', 100: '#e0f2fe', 400: '#38bdf8', 500: '#0ea5e9', 600: '#0284c7', 700: '#0369a1', 800: '#075985', 900: '#0c4a6e', 950: '#082f49' } },
        fontFamily: { sans: ['Inter', 'system-ui', 'sans-serif'] }
    } }
};
