import numpy as np


class ImageHash:
    def __init__(self, binary_array: np.ndarray) -> None:
        self._hash = binary_array

    def __len__(self) -> int:
        return len(self._hash)

    def __repr__(self) -> str:
        return _binary_array_to_hex(self._hash)

    def __sub__(self, other) -> float:
        if other is None:
            raise TypeError('Other hash must not be None.')
        elif self._hash.size != other._hash.size:
            raise TypeError('ImageHashes must be of the same shape.', self._hash.shape, other._hash.shape)

        return np.count_nonzero(self._hash != other._hash) / len(self._hash)

    def __eq__(self, other) -> bool:
        if other is None:
            return False
        return np.array_equal(self._hash, other._hash)

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)


class ImageMultiHash:
    def __init__(self, hashes: list[ImageHash]) -> None:
        self._hashes = hashes

    def __len__(self) -> int:
        return sum([len(_hash) for _hash in sorted(str(_hash) for _hash in self._hashes)])

    def __repr__(self) -> str:
        sorted_hashes = sorted(str(_hash) for _hash in self._hashes)
        return ','.join(str(_hash) for _hash in sorted_hashes)

    def __sub__(self, other) -> float:
        if other is None:
            raise TypeError('Other hash must not be None.')
        if not isinstance(other, ImageMultiHash):
            raise TypeError('Other must be an instance of ImageMultiHash.')

        if len(self._hashes) == 0 or len(other._hashes) == 0:
            raise ValueError("Hashes must not be empty.")

        differences = []
        for hash_self in self._hashes:
            hash_self_differences = []
            for hash_other in other._hashes:
                try:
                    hash_self_differences.append(hash_self - hash_other)
                except Exception:
                    hash_self_differences.append(1)

            differences.append(min(hash_self_differences))

        for hash_other in other._hashes:
            hash_other_differences = []
            for hash_self in self._hashes:
                try:
                    hash_other_differences.append(hash_other - hash_self)
                except Exception:
                    hash_other_differences.append(1)

            differences.append(min(hash_other_differences))

        return np.mean(differences)

    def __eq__(self, other) -> bool:
        if other is None or not isinstance(other, ImageMultiHash):
            return False
        if len(self._hashes) != len(other._hashes):
            return False

        sorted_self_hashes = sorted(str(_hash) for _hash in self._hashes)
        sorted_other_hashes = sorted(str(_hash) for _hash in other._hashes)
        return sorted_self_hashes == sorted_other_hashes

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)


def _binary_array_to_hex(binary_array) -> str:
    binary_string = ''.join(str(b) for b in binary_array)
    return ''.join(format(int(binary_string[i: i + 4], 2), 'x') for i in range(0, len(binary_string), 4))


def hex_to_hash(hex_string) -> ImageHash:
    binary_string = bin(int(hex_string, 16))[2:].zfill(len(hex_string) * 4)
    return ImageHash(np.array([int(bit) for bit in binary_string]))


def hex_to_multihash(hex_string):
    hashes = [hex_to_hash(x) for x in hex_string.split(',')]
    return ImageMultiHash(hashes)
