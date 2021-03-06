# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
from collections import OrderedDict

from typechecks import require_instance
from serializable import Serializable
from pepdata.amino_acid_alphabet import (
    amino_acid_letter_indices,
    canonical_amino_acids
)
from pepdata.pmbec import pmbec_matrix
from pepdata.blosum import blosum62_matrix


class Encoder(Serializable):
    """
    Container for mapping between amino acid letter codes and their full names
    and providing index/hotshot encodings of amino acid sequences.

    The reason we need a class to contain these mappins is that we might want to
    operate on just the common 20 amino acids or a richer set with modifications.
    """
    def __init__(
            self,
            amino_acid_alphabet=canonical_amino_acids,
            variable_length_sequences=True,
            add_start_tokens=False,
            add_stop_tokens=False,
            add_normalized_position=False,
            add_normalized_centrality=False):
        """
        Parameters
        ----------
        tokens_to_names_dict : dict
            Dictionary mapping each amino acid to its name

        variable_length_sequences : bool
            Do we expect to encode peptides of varying lengths? If so, include
            the gap token "-" in the encoder's alphabet.

        add_start_tokens : bool
            Prefix each peptide string with "^"

        add_stop_tokens : bool
            End each peptide string with "$"

        add_normalized_position : bool
            Extend the representation of each residue with where it is in
            the sequence from left-to-right (scaled to be between 0 and 1)

        add_normalized_centrality : bool
            Extend the representation of each residue with how close it is
            to the center. 0 represents left and right edges of the sequence,
            1.0 represents the center.

        """
        self._tokens_to_names = OrderedDict()
        self._index_dict = {}

        self.amino_acid_alphabet = amino_acid_alphabet
        self.variable_length_sequences = variable_length_sequences
        self.add_start_tokens = add_start_tokens
        self.add_stop_tokens = add_stop_tokens
        self.add_normalized_position = add_normalized_position
        self.add_normalized_centrality = add_normalized_centrality

        if self.variable_length_sequences:
            self._add_token("-", "Gap")

        if self.add_start_tokens:
            self._add_token("^", "Start")

        if self.add_stop_tokens:
            self._add_token("$", "Stop")

        for aa in amino_acid_alphabet:
            self._add_token(aa.letter, aa.full_name)

    def _add_token(self, token, name):
        assert len(token) == 1, "Invalid token '%s'" % (token,)
        assert token not in self._index_dict
        assert token not in self._tokens_to_names
        self._index_dict[token] = len(self._index_dict)
        self._tokens_to_names[token] = name

    def prepare_sequences(self, peptides, padded_peptide_length=None):
        """
        Add start/stop tokens to each peptide (if required) and
        if padded_peptide_length is provided then pad each peptide to
        be the same length using the gap token '-'.
        """
        if self.add_start_tokens:
            peptides = ["^" + p for p in peptides]
            if padded_peptide_length:
                padded_peptide_length += 1

        if self.add_stop_tokens:
            peptides = [p + "$" for p in peptides]
            if padded_peptide_length:
                padded_peptide_length += 1

        if padded_peptide_length:
            peptides = [
                p + "-" * (padded_peptide_length - len(p))
                for p in peptides
            ]
        return peptides

    @property
    def tokens(self):
        """
        Return letters in sorted order, special characters should get indices
        lower than actual amino acids in this order:
            1) "-"
            2) "^"
            3) "$"
        Currently we're enforcing this order by having the _tokens_to_names
        dictionary be an OrderedDict and adding special tokens before amino
        acids in the __init__ method.
        """
        return list(self._tokens_to_names.keys())

    @property
    def amino_acid_names(self):
        return [self._tokens_to_names[k] for k in self.tokens]

    @property
    def index_dict(self):
        return self._index_dict

    def __getitem__(self, k):
        return self._tokens_to_names[k]

    def __setitem__(self, k, v):
        self._add_token(k, v)

    def __len__(self):
        return len(self.tokens)

    def _validate_peptide_lengths(
            self,
            peptides,
            max_peptide_length=None):
        require_instance(peptides, (list, tuple, np.ndarray))
        if max_peptide_length is None:
            max_peptide_length = max(len(p) for p in peptides)

        if self.variable_length_sequences:
            max_observed_length = max(len(p) for p in peptides)
            if max_observed_length > max_peptide_length:
                example = [p for p in peptides if len(p) == max_observed_length][0]
                raise ValueError(
                    "Peptide(s) of length %d when max = %d (example '%s')" % (
                        max_observed_length,
                        max_peptide_length,
                        example))
        elif any(len(p) != max_peptide_length for p in peptides):
            example = [p for p in peptides if len(p) != max_peptide_length][0]
            raise ValueError("Expected all peptides to have length %d, '%s' has length %d" % (
                max_peptide_length,
                example,
                len(example)))
        return max_peptide_length

    def _validate_and_prepare_peptides(self, peptides, max_peptide_length=None):
        max_peptide_length = self._validate_peptide_lengths(
            peptides, max_peptide_length)
        peptides = self.prepare_sequences(peptides)
        # did we add start tokens to each sequence?
        max_peptide_length += self.add_start_tokens
        # did we add stop tokens to each sequence?
        max_peptide_length += self.add_stop_tokens
        return peptides, max_peptide_length

    def encode_index_lists(self, peptides):
        # don't try to do length validation since we're allowed to have
        # multiple peptide lengths
        peptides = self.prepare_sequences(peptides)
        index_dict = self.index_dict
        return [
            [index_dict[amino_acid] for amino_acid in peptide]
            for peptide in peptides
        ]

    def encode_index_array(
            self,
            peptides,
            max_peptide_length=None):
        """
        Encode a set of equal length peptides as a matrix of their
        amino acid indices.
        """
        assert not self.add_normalized_centrality
        assert not self.add_normalized_position
        peptides, max_peptide_length = self._validate_and_prepare_peptides(
            peptides, max_peptide_length)
        n_peptides = len(peptides)
        X_index = np.zeros((n_peptides, max_peptide_length), dtype=int)
        index_dict = self.index_dict
        for i, peptide in enumerate(peptides):
            for j, amino_acid in enumerate(peptide):
                # we're expecting the token '-' to have index 0 so it's
                # OK to only loop until the end of the given sequence
                X_index[i, j] = index_dict[amino_acid]
        return X_index

    def _add_extra_features(self, X, peptides):
        if not self.add_normalized_position and not self.add_normalized_centrality:
            return X
        lengths = np.array([len(p) for p in peptides])
        n = len(X)
        max_length = lengths.max()

        X_centrality = np.zeros((n, max_length), dtype="float32")
        X_position = np.zeros((n, max_length), dtype="float32")
        for i, l in enumerate(lengths):
            center = (l - 1) / 2
            vec = np.arange(l)
            X_centrality[i, :l] = np.abs(vec - center) / center
            X_position[i, :] = vec / l
        extra_arrays = []
        if self.add_normalized_centrality:
            extra_arrays.append(X_centrality)
        if self.add_normalized_position:
            extra_arrays.append(X_position)
        return np.dstack([X] + extra_arrays)

    def _encode_from_pairwise_properties(
            self, peptides, max_peptide_length, property_matrix):
        peptides, max_peptide_length = self._validate_and_prepare_peptides(
            peptides, max_peptide_length)
        n_peptides = len(peptides)
        shape = (n_peptides, max_peptide_length, 20)
        X = np.zeros(shape, dtype="float32")
        aa_to_feature_row = {}
        alphabet_indices = [
            amino_acid_letter_indices[aa.letter]
            for aa in self.amino_acid_alphabet
        ]
        for aa in self.amino_acid_alphabet:
            aa_idx = amino_acid_letter_indices[aa.letter]
            row = property_matrix[aa_idx, :]
            row = row[alphabet_indices]
            aa_to_feature_row[aa.letter] = row

        # add zero vectors for gap, start and stop tokens
        for token in ["-", "^", "$"]:
            aa_to_feature_row[token] = np.zeros(
                len(alphabet_indices), dtype="float32")
        for i, peptide in enumerate(peptides):
            for j, amino_acid in enumerate(peptide):
                X[i, j, :] = aa_to_feature_row[amino_acid]
        return self._add_extra_features(X, peptides)

    def encode_pmbec(self, peptides, max_peptide_length=None):
        return self._encode_from_pairwise_properties(
            peptides=peptides,
            max_peptide_length=max_peptide_length,
            property_matrix=pmbec_matrix)

    def encode_blosum(self, peptides, max_peptide_length=None):
        return self._encode_from_pairwise_properties(
            peptides=peptides,
            max_peptide_length=max_peptide_length,
            property_matrix=blosum62_matrix)

    def encode_onehot(
            self,
            peptides,
            max_peptide_length=None):
        """
        Encode a set of equal length peptides as a binary matrix,
        where each letter is transformed into a length 20 vector with a single
        element that is 1 (and the others are 0).
        """
        peptides, max_peptide_length = self._validate_and_prepare_peptides(
            peptides, max_peptide_length)
        index_dict = self.index_dict
        n_symbols = len(index_dict)
        X = np.zeros((len(peptides), max_peptide_length, n_symbols), dtype=bool)
        for i, peptide in enumerate(peptides):
            for j, amino_acid in enumerate(peptide):
                X[i, j, index_dict[amino_acid]] = 1
        return self._add_extra_features(X, peptides)

    def encode_FOFE(self, peptides, alpha=0.7, bidirectional=False):
        """
        Implementation of FOFE encoding from:
            A Fixed-Size Encoding Method for Variable-Length Sequences with its
            Application to Neural Network Language Models

        Parameters
        ----------
        peptides : list of strings

        alpha: float
            Forgetting factor

        bidirectional: boolean
            Whether to do both a forward pass and a backward pass over each
            peptide
        """
        # don't try to do length validation since we're allowed to have
        # multiple peptide lengths in a FOFE encoding
        peptides = self.prepare_sequences(peptides)
        n_peptides = len(peptides)
        index_dict = self.index_dict
        n_symbols = len(index_dict)
        if bidirectional:
            result = np.zeros((n_peptides, 2 * n_symbols), dtype=float)
        else:
            result = np.zeros((n_peptides, n_symbols), dtype=float)
        for i, p in enumerate(peptides):
            l = len(p)
            for j, amino_acid in enumerate(p):
                aa_idx = index_dict[amino_acid]
                result[i, aa_idx] += alpha ** (l - j - 1)
                if bidirectional:
                    result[i, n_symbols + aa_idx] += alpha ** j
        return result
