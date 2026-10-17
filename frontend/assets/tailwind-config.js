// Configuração do Tailwind (CDN) compartilhada pelas páginas. Carregar DEPOIS do script do CDN.
tailwind.config = {
    darkMode: 'class',
    theme: { extend: {
        colors: { brand: { 50: '#fdf2f0', 100: '#fbe0dc', 400: '#e0584c', 500: '#cc3b2f', 600: '#A92823', 700: '#8f1f17', 800: '#6e1812', 900: '#4f110d', 950: '#2c0907' } },
        fontFamily: { sans: ['Space Grotesk', 'Inter', 'system-ui', 'sans-serif'] }
    } }
};
