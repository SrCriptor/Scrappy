<?php
// Função para pegar o conteúdo HTML da URL e extrair os links dos arquivos de mídia
function getMediaLinks($url) {
    // Usando cURL para obter o conteúdo da página
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, 1);
    $content = curl_exec($ch);
    curl_close($ch);

    // Verifica se o conteúdo foi recuperado com sucesso
    if (!$content) {
        // Retorna array vazio para manter consistência no tipo de retorno
        return [];
    }

    // Usando expressão regular para capturar links de arquivos de mídia (sem áudio)
    $mediaLinks = [];
    preg_match_all('/(?:src|href)="(https?:\/\/[^"]*\.(?:mp4|webm|ogg|png|jpg|jpeg|gif|webp|bmp|flv|avi))"/i', $content, $matches);

    // Adiciona todos os links encontrados à lista
    foreach ($matches[1] as $link) {
        $mediaLinks[] = $link;
    }

    return $mediaLinks;
}

// Função para exibir visualizações de imagens (se for imagem)
function displayImagePreview($url) {
    $image = @getimagesize($url); // @ para suprimir warning caso URL inválida
    return $image ? "<img src='$url' alt='Imagem' style='max-width: 200px; margin: 10px;'>" : '';
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $url = trim($_POST['url'] ?? '');

    if (empty($url)) {
        $errorMessage = "Por favor, insira um URL.";
        $mediaLinks = [];
    } elseif (!filter_var($url, FILTER_VALIDATE_URL)) {
        $errorMessage = "URL inválida. Por favor, informe uma URL correta.";
        $mediaLinks = [];
    } else {
        // Pegando os links de mídia do site
        $mediaLinks = getMediaLinks($url);

        // Se por algum motivo não for array, define array vazio para evitar erros
        if (!is_array($mediaLinks)) {
            $errorMessage = "Erro ao buscar os links de mídia.";
            $mediaLinks = [];
        }

        // Se não encontrar nenhum link, pode avisar também
        if (empty($mediaLinks)) {
            $infoMessage = "Nenhum arquivo de mídia encontrado.";
        }
    }
}
?>

<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Scraper de Mídia</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        form { margin-bottom: 20px; }
        .media-item { margin-bottom: 15px; }
        .video { margin-top: 10px; }
        .error { color: red; }
        .info { color: blue; }
    </style>
</head>
<body>

    <h1>Extrair Links de Mídia</h1>
    <form method="POST">
        <label for="url">Informe a URL do site:</label>
        <input type="text" id="url" name="url" value="<?= htmlspecialchars($url ?? '') ?>" required>
        <button type="submit">Buscar Links</button>
    </form>

    <?php if (!empty($errorMessage)): ?>
        <p class="error"><?= htmlspecialchars($errorMessage) ?></p>
    <?php elseif (!empty($infoMessage)): ?>
        <p class="info"><?= htmlspecialchars($infoMessage) ?></p>
    <?php endif; ?>

    <?php if (!empty($mediaLinks)): ?>
        <h2>Links Encontrados:</h2>
        <ul>
            <?php foreach ($mediaLinks as $link): ?>
                <li class="media-item">
                    <strong>Link:</strong> <a href="<?= htmlspecialchars($link) ?>" target="_blank"><?= htmlspecialchars($link) ?></a><br>
                    <?php if (preg_match('/\.(png|jpg|jpeg|gif|webp|bmp)$/i', $link)): ?>
                        <div class="image-preview">
                            <?= displayImagePreview($link) ?>
                        </div>
                    <?php elseif (preg_match('/\.(mp4|webm|ogg|flv|avi)$/i', $link)): ?>
                        <div class="video">
                            <strong>Vídeo:</strong><br>
                            <video width="320" height="240" controls>
                                <source src="<?= htmlspecialchars($link) ?>" type="video/<?= pathinfo($link, PATHINFO_EXTENSION) ?>">
                                Seu navegador não suporta a tag de vídeo.
                            </video>
                        </div>
                    <?php endif; ?>
                </li>
            <?php endforeach; ?>
        </ul>
    <?php endif; ?>

</body>
</html>
