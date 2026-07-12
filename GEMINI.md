# Changements et choses testées

- Installation de Node.js version 24 via NVM (`nvm install 24`).
- Configuration de Node 24 comme version par défaut dans nvm (`nvm alias default 24`).
- Remplacement des textes gris clair (text-gray-400 à 600) par text-black dans les templates HTML pour améliorer la lisibilité en mode clair.
- Remplacement exhaustif de tous les textes Tailwind (text-gray-*, text-slate-*, text-zinc-*, text-neutral-*, text-stone-*) ayant une luminosité de 100 à 900 par du texte noir absolu (text-black) pour garantir une lisibilité optimale sur fond blanc partout.
- Nouveaux remplacements drastiques : Tous les textes gris/slate/zinc/neutral/stone ont été passés en text-black. Les couleurs dark:text-black (qui seraient invisibles en mode sombre) ont été passées en dark:text-white. L'opacité des ombres a été augmentée pour plus de contraste.
- Correction d'un bug d'affichage où le CSS des marqueurs Leaflet et étiquettes DPE/GES s'affichait sous forme de texte en bas de la page : le CSS a été correctement déplacé dans la balise `<style>` de `base.html`.
