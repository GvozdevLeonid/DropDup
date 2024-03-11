from .image_hash import (
    ImageMultiHash,
    ImageHash
)
from PIL import (
    ImageFilter,
    Image,
)
try:
    ANTIALIAS = Image.Resampling.LANCZOS
except AttributeError:
    ANTIALIAS = Image.ANTIALIAS

import numpy as np


def __is_power_of_two(n) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def ahash(image: Image, hash_size: int = 8) -> ImageHash:

    if not __is_power_of_two(hash_size):
        raise ValueError('Hash size is not power of 2')

    # image = image.convert('L').resize((hash_size, hash_size), ANTIALIAS)
    image = image.convert('YCbCr').split()[0].filter(ImageFilter.MedianFilter()).resize((hash_size, hash_size), ANTIALIAS)

    pixels = np.asarray(image)
    mean = np.mean(pixels)
    diff = pixels >= mean

    return ImageHash(diff.astype(dtype=np.int8).flatten())


def rhash(image: Image, hash_size: int = 8, block_size: int = 4) -> ImageHash:
    if not __is_power_of_two(hash_size):
        raise ValueError('Hash size is not power of 2')

    if not __is_power_of_two(block_size):
        raise ValueError('Block size is not power of 2')

    image_size = hash_size * block_size
    # image = image.convert('L').filter(ImageFilter.GaussianBlur()).resize((image_size, image_size), ANTIALIAS)
    image = image.convert('YCbCr').split()[0].filter(ImageFilter.MedianFilter()).resize((image_size, image_size), ANTIALIAS)
    pixels = np.asarray(image)

    binary_array = []
    for i in range(hash_size):
        for j in range(hash_size):
            istart, iend = i * block_size, (i + 1) * block_size
            jstart, jend = j * block_size, (j + 1) * block_size

            block = pixels[istart: iend, jstart: jend]
            binary_array.append(np.mean(block))

    mean_block_size = len(binary_array) // 4
    binary_array = np.array(binary_array)
    means = []
    for i in range(4):
        mean = np.mean(binary_array[i * mean_block_size: (i + 1) * mean_block_size])
        means.append(mean)
        binary_array[i * mean_block_size: (i + 1) * mean_block_size] = binary_array[i * mean_block_size: (i + 1) * mean_block_size] >= mean

    min_idx = means.index(min(means))
    if min_idx == 1:  # правый верхний угол
        binary_array_2d = binary_array.reshape((hash_size, hash_size))
        binary_array = np.fliplr(binary_array_2d)
    elif min_idx == 2:  # левый нижний угол
        binary_array_2d = binary_array.reshape((hash_size, hash_size))
        binary_array = np.flipud(binary_array_2d)
    elif min_idx == 3:  # правый нижний угол
        binary_array_2d = binary_array.reshape((hash_size, hash_size))
        binary_array = np.fliplr(np.flipud(binary_array_2d))

    return ImageHash(binary_array.astype(dtype=np.int8).flatten())


def phash(image: Image, hash_size: int = 8, highfreq_factor: int = 4) -> ImageHash:
    if not __is_power_of_two(hash_size):
        raise ValueError('Hash size is not power of 2')

    if not __is_power_of_two(highfreq_factor):
        raise ValueError('Highfreq factor is not power of 2')

    from scipy.fftpack import dct

    image_size = hash_size * highfreq_factor
    image = image.convert('YCbCr').split()[0].filter(ImageFilter.MedianFilter()).resize((image_size, image_size), ANTIALIAS)
    # image = image.convert('L').filter(ImageFilter.MedianFilter()).resize((image_size, image_size), ANTIALIAS)
    pixels = np.asarray(image)
    dct = dct(dct(pixels, axis=0), axis=1)
    dctlowfreq = dct[1: hash_size + 1, 1: hash_size + 1]
    mean = np.mean(dctlowfreq)
    diff = dctlowfreq >= mean

    return ImageHash(diff.astype(dtype=np.int8).flatten())


def dhash_horizontal(image: Image, hash_size: int = 8) -> ImageHash:
    if not __is_power_of_two(hash_size):
        raise ValueError('Hash size is not power of 2')

    # image = image.convert('L').resize((hash_size + 1, hash_size), ANTIALIAS)
    image = image.convert('YCbCr').split()[0].filter(ImageFilter.MedianFilter()).resize((hash_size + 1, hash_size), ANTIALIAS)
    pixels = np.asarray(image)
    diff = pixels[:, 1:] >= pixels[:, :-1]

    return ImageHash(diff.astype(dtype=np.int8).flatten())


def dhash_vertical(image: Image, hash_size: int = 8) -> ImageHash:
    if not __is_power_of_two(hash_size):
        raise ValueError('Hash size is not power of 2')

    # image = image.convert('L').resize((hash_size, hash_size + 1), ANTIALIAS)
    image = image.convert('YCbCr').split()[0].filter(ImageFilter.MedianFilter()).resize((hash_size, hash_size + 1), ANTIALIAS)
    pixels = np.asarray(image)
    diff = pixels[1:, :] >= pixels[:-1, :]

    return ImageHash(diff.astype(dtype=np.int8).flatten())


def dhash(image: Image, hash_size: int = 16) -> ImageHash:
    # image = image.convert('L').resize((hash_size + 1, hash_size + 1), ANTIALIAS)
    image = image.convert('YCbCr').split()[0].filter(ImageFilter.MedianFilter()).resize((hash_size + 1, hash_size + 1), ANTIALIAS)
    pixels = np.asarray(image)

    diff_h = pixels[:, 1:] >= pixels[:, :-1]
    diff_v = pixels[1:, :] >= pixels[:-1, :]
    diff = diff_h.flatten() & diff_v.flatten()

    return ImageHash(diff.astype(dtype=np.int8).flatten())


def colorhash(image, binbits: int = 4) -> ImageHash:

    intensity = np.asarray(image.convert('L')).flatten()
    h, s, v = [np.asarray(v).flatten() for v in image.convert('HSV').split()]

    mask_black = intensity < 256 // 8
    frac_black = mask_black.mean()

    mask_gray = s < 256 // 3
    frac_gray = np.logical_and(~mask_black, mask_gray).mean()

    mask_colors = np.logical_and(~mask_black, ~mask_gray)
    mask_faint_colors = np.logical_and(mask_colors, s < 256 * 2 // 3)
    mask_bright_colors = np.logical_and(mask_colors, s > 256 * 2 // 3)

    c = max(1, mask_colors.sum())

    hue_bins = np.linspace(0, 255, 6 + 1)
    if mask_faint_colors.any():
        h_faint_counts, _ = np.histogram(h[mask_faint_colors], bins=hue_bins)
    else:
        h_faint_counts = np.zeros(len(hue_bins) - 1)
    if mask_bright_colors.any():
        h_bright_counts, _ = np.histogram(h[mask_bright_colors], bins=hue_bins)
    else:
        h_bright_counts = np.zeros(len(hue_bins) - 1)

    maxvalue = 2 ** binbits
    values = [min(maxvalue - 1, int(frac_black * maxvalue)), min(maxvalue - 1, int(frac_gray * maxvalue))]
    for counts in list(h_faint_counts) + list(h_bright_counts):
        values.append(min(maxvalue - 1, int(counts * maxvalue * 1. / c)))

    bitarray = []
    for v in values:
        bitarray += [v // (2 ** (binbits - i - 1)) % 2 ** (binbits - i) > 0 for i in range(binbits)]

    return ImageHash(np.asarray(bitarray, dtype=np.int8).reshape((-1, binbits)).flatten())


def _find_region(remaining_pixels, segmented_pixels):
    in_region = set()
    not_in_region = set()
    available_pixels = np.transpose(np.nonzero(remaining_pixels))
    start = tuple(available_pixels[0])
    in_region.add(start)
    new_pixels = in_region.copy()
    while True:
        try_next = set()
        for pixel in new_pixels:
            x, y = pixel
            neighbours = [(x - 1, y),
                          (x + 1, y),
                          (x, y - 1),
                          (x, y + 1)
                          ]
            try_next.update(neighbours)
        try_next.difference_update(segmented_pixels, not_in_region)
        if not try_next:
            break
        new_pixels = set()
        for pixel in try_next:
            if remaining_pixels[pixel]:
                in_region.add(pixel)
                new_pixels.add(pixel)
                segmented_pixels.add(pixel)
            else:
                not_in_region.add(pixel)
    return in_region


def _find_all_segments(pixels, segment_threshold, min_segment_size):
    img_width, img_height = pixels.shape

    threshold_pixels = pixels > segment_threshold
    unassigned_pixels = np.full(pixels.shape, True, dtype=bool)

    segments = []
    already_segmented = set()

    already_segmented.update([(-1, z) for z in range(img_height)])
    already_segmented.update([(z, -1) for z in range(img_width)])
    already_segmented.update([(img_width, z) for z in range(img_height)])
    already_segmented.update([(z, img_height) for z in range(img_width)])

    while np.bitwise_and(threshold_pixels, unassigned_pixels).any():
        remaining_pixels = np.bitwise_and(threshold_pixels, unassigned_pixels)
        segment = _find_region(remaining_pixels, already_segmented)
        if len(segment) > min_segment_size:
            segments.append(segment)
        for pix in segment:
            unassigned_pixels[pix] = False

    threshold_pixels_i = np.invert(threshold_pixels)
    while len(already_segmented) < img_width * img_height:
        remaining_pixels = np.bitwise_and(threshold_pixels_i, unassigned_pixels)
        segment = _find_region(remaining_pixels, already_segmented)
        if len(segment) > min_segment_size:
            segments.append(segment)
        for pix in segment:
            unassigned_pixels[pix] = False

    return segments


def crop_resistant_hash(
        image: Image,
        hash_func=None,
        limit_segments: int = None,
        segment_threshold: int = 128,
        min_segment_size: int = 300,
        segmentation_image_size: int = 600,
        **kwargs):

    if hash_func is None:
        hash_func = dhash

    orig_image = image.copy()
    # image = image.convert('L').resize((segmentation_image_size, segmentation_image_size), ANTIALIAS)
    image = image.convert('YCbCr').split()[0].resize((segmentation_image_size, segmentation_image_size), ANTIALIAS)
    image = image.filter(ImageFilter.GaussianBlur()).filter(ImageFilter.MedianFilter())
    pixels = np.array(image).astype(np.float32)

    segments = _find_all_segments(pixels, segment_threshold, min_segment_size)

    if not segments:
        full_image_segment = {(0, 0), (segmentation_image_size - 1, segmentation_image_size - 1)}
        segments.append(full_image_segment)

    if limit_segments:
        segments = sorted(segments, key=lambda s: len(s), reverse=True)[:limit_segments]

    hashes = []
    for segment in segments:
        orig_w, orig_h = orig_image.size
        scale_w = float(orig_w) / segmentation_image_size
        scale_h = float(orig_h) / segmentation_image_size
        min_y = min(coord[0] for coord in segment) * scale_h
        min_x = min(coord[1] for coord in segment) * scale_w
        max_y = (max(coord[0] for coord in segment) + 1) * scale_h
        max_x = (max(coord[1] for coord in segment) + 1) * scale_w
        bounding_box = orig_image.crop((min_x, min_y, max_x, max_y))
        hashes.append(hash_func(bounding_box, **kwargs))

    return ImageMultiHash(hashes)
