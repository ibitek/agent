export const imageTypes = ['image/png', 'image/jpeg']
const maxUploadBytes = 1024 * 1024
const maxAssetLength = 256 * 1024

export function readReferenceImage(file) {
  if (!imageTypes.includes(file.type) || file.size > maxUploadBytes) {
    return Promise.reject(new Error('Use a PNG or JPEG image up to 1 MB.'))
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve({ name: file.name || 'Clipboard image', url: reader.result })
    reader.onerror = () => reject(new Error('Could not read the image.'))
    reader.readAsDataURL(file)
  })
}

export function generatedImageUrl(id) {
  return '/api/media/images/' + encodeURIComponent(id)
}

export async function prepareMaskedEdit(imageUrl, regions) {
  const selected = regions.filter(region => region.note.trim())
  if (!selected.length) return { image: imageUrl }
  const image = new Image()
  image.src = imageUrl
  await image.decode()
  const scale = Math.min(1, 1280 / Math.max(image.naturalWidth, image.naturalHeight))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale))
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale))
  const context = canvas.getContext('2d')
  if (!context) throw new Error('This browser could not prepare the edit mask.')
  context.fillStyle = '#fff'
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  const source = canvas.toDataURL('image/jpeg', .8)
  context.fillRect(0, 0, canvas.width, canvas.height)
  for (const { x, y, width, height } of selected) {
    const left = Math.floor(x * canvas.width)
    const top = Math.floor(y * canvas.height)
    const right = Math.ceil((x + width) * canvas.width)
    const bottom = Math.ceil((y + height) * canvas.height)
    context.clearRect(left, top, right - left, bottom - top)
  }
  return { image: source, mask: canvas.toDataURL('image/png') }
}

function wrapImage(dataUrl, width, height) {
  return '<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height +
    '" viewBox="0 0 ' + width + ' ' + height + '"><image width="' + width + '" height="' + height +
    '" href="' + dataUrl + '"/></svg>'
}

export async function makeProjectImage(dataUrl) {
  if (!/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/.test(dataUrl)) {
    throw new Error('This image cannot be saved as a project asset.')
  }
  const image = new Image()
  image.src = dataUrl
  await image.decode().catch(() => { throw new Error('The image could not be opened.') })
  const { naturalWidth: width, naturalHeight: height } = image
  const original = wrapImage(dataUrl, width, height)
  if (original.length <= maxAssetLength) return { content: original, resized: false }

  // Project snapshots store text. Keep the embedded raster in its original format.
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  if (!context) throw new Error('This browser could not prepare the project image.')
  const type = dataUrl.startsWith('data:image/png') ? 'image/png' : 'image/jpeg'
  let scale = Math.min(0.85, 1800 / Math.max(width, height))
  for (let attempt = 0; attempt < 12; attempt += 1) {
    canvas.width = Math.max(1, Math.round(width * scale))
    canvas.height = Math.max(1, Math.round(height * scale))
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const content = wrapImage(canvas.toDataURL(type, 0.9), canvas.width, canvas.height)
    if (content.length <= maxAssetLength) return { content, resized: true }
    scale *= 0.76
  }
  throw new Error('The image could not fit within the project file limit.')
}
